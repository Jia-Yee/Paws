# -*- coding: utf-8 -*-
# pylint: disable=too-many-statements,too-many-branches
"""ESP32 Channel for CoPaw.

This channel enables ESP32 devices to communicate with CoPaw via WebSocket
using the Xiaozhi protocol for voice and text interaction.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import wave  # Added for debug audio saving
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import numpy as np  # Added for PCM analysis
import websockets
from websockets.server import serve

from ..base import (
    BaseChannel,
    ContentType,
    OnReplySent,
    OutgoingContentPart,
    ProcessHandler,
    TextContent,
)
from .constants import AudioFormat, DeviceState, ListenState, MessageType, TTSState
from .protocol import (
    AudioMessage,
    ErrorMessage,
    HelloMessage,
    ListenMessage,
    LLMMessage,
    STTMessage,
    StateMessage,
    TextMessage,
    TTSMessage,
    XiaozhiMessage,
    XiaozhiProtocol,
)
from .utils import calculate_audio_duration_ms, pcm_to_wav, resample_audio
from ....voice import VoiceProcessor, VoiceProcessorConfig
from ....voice.opus import OpusDecoder, OpusEncoder, OpusCodecError

if TYPE_CHECKING:
    from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest




class AudioBuffer:
    """智能音频缓冲区，支持环形缓冲和时间戳管理"""
    def __init__(self, max_size_bytes: int = 1024 * 1024):
        self._buffer: List[bytes] = []
        self._timestamps: List[float] = []
        self._max_size_bytes = max_size_bytes
        self._current_size_bytes = 0
        self._last_cleanup_time = time.time()
    
    def add(self, audio_data: bytes, timestamp: float = None):
        """添加音频数据到缓冲区"""
        if timestamp is None:
            timestamp = time.time()
        
        # 避免添加空数据
        if not audio_data:
            return
        
        self._buffer.append(audio_data)
        self._timestamps.append(timestamp)
        self._current_size_bytes += len(audio_data)
        
        # 保持缓冲区大小在限制内
        while self._current_size_bytes > self._max_size_bytes:
            removed_data = self._buffer.pop(0)
            self._timestamps.pop(0)
            self._current_size_bytes -= len(removed_data)
        
        # 定期清理过期数据（每10秒）
        current_time = time.time()
        if current_time - self._last_cleanup_time > 10:
            self._cleanup_old_data(current_time - 30)  # 清理30秒前的数据
            self._last_cleanup_time = current_time
    
    def get_all(self) -> bytes:
        """获取所有缓冲的音频数据"""
        if not self._buffer:
            return b""
        # 优化：直接连接所有数据，避免中间拷贝
        return b"" .join(self._buffer)
    
    def get_since(self, timestamp: float) -> bytes:
        """获取指定时间戳之后的音频数据"""
        if not self._buffer:
            return b""
        idx = 0
        while idx < len(self._timestamps) and self._timestamps[idx] < timestamp:
            idx += 1
        if idx >= len(self._buffer):
            return b""
        return b"" .join(self._buffer[idx:])
    
    def clear(self):
        """清空缓冲区"""
        # 优化：使用clear()而不是重新分配
        self._buffer.clear()
        self._timestamps.clear()
        self._current_size_bytes = 0
    
    def size(self) -> int:
        """返回缓冲区中的数据大小（字节）"""
        return self._current_size_bytes
    
    def count(self) -> int:
        """返回缓冲区中的数据块数量"""
        return len(self._buffer)
    
    def _cleanup_old_data(self, cutoff_time: float):
        """清理指定时间之前的数据"""
        if not self._buffer:
            return
        
        idx = 0
        while idx < len(self._timestamps) and self._timestamps[idx] < cutoff_time:
            idx += 1
        
        if idx > 0:
            # 移除过期数据
            removed_data = self._buffer[:idx]
            self._buffer = self._buffer[idx:]
            self._timestamps = self._timestamps[idx:]
            self._current_size_bytes -= sum(len(data) for data in removed_data)


class ESP32DeviceStateMachine:
    """ESP32设备状态机，管理设备状态转换"""
    def __init__(self, logger):
        self.state: DeviceState = DeviceState.IDLE
        self._state_history: List[DeviceState] = []
        self.logger = logger
    
    def transition_to(self, new_state: DeviceState) -> bool:
        """转换到新状态"""
        if new_state != self.state:
            self._state_history.append(self.state)
            self.state = new_state
            self.logger.info(f"State transition: {self._state_history[-1]} -> {new_state}")
            return True
        return False
    
    def get_state(self) -> DeviceState:
        """获取当前状态"""
        return self.state
    
    def is_idle(self) -> bool:
        """检查设备是否处于空闲状态"""
        return self.state == DeviceState.IDLE
    
    def is_listening(self) -> bool:
        """检查设备是否处于监听状态"""
        return self.state == DeviceState.LISTENING
    
    def is_processing(self) -> bool:
        """检查设备是否处于处理状态"""
        return self.state == DeviceState.PROCESSING
    
    def is_speaking(self) -> bool:
        """检查设备是否处于 speaking 状态"""
        return self.state == DeviceState.SPEAKING


class ESP32DeviceConnection:
    def __init__(
        self,
        device_id: str,
        websocket: websockets.ServerConnection,
        protocol: XiaozhiProtocol,
        logger,
    ):
        self.device_id = device_id
        self.websocket = websocket
        self.protocol = protocol
        self.logger = logger
        self.state_machine: ESP32DeviceStateMachine = ESP32DeviceStateMachine(logger)
        self.client_id: str = ""
        self.version: str = ""
        self.session_id: str = ""
        self.audio_config: Dict[str, Any] = {}
        self.last_activity: float = time.time()
        self.voice_processor: Optional[VoiceProcessor] = None
        self._audio_buffer: AudioBuffer = AudioBuffer()
        self._is_aborted: bool = False
        self._speaking_task: Optional[asyncio.Task] = None
        self._opus_decoder: Optional[OpusDecoder] = None
        self._opus_encoder: Optional[OpusEncoder] = None
        self._new_session_event: asyncio.Event = asyncio.Event()  # 🔧 新会话事件标志
        self._last_ping_time: float = time.time()  # 上次ping时间
        self._ping_task: Optional[asyncio.Task] = None  # 心跳任务
        self._connection_established: bool = False  # 连接是否已建立
        self._wake_word_detected: bool = False  # 唤醒词是否已检测
        self._wake_word_timestamp: float = 0.0  # 唤醒词检测时间戳
        self._audio_sequence: int = 0  # 音频序列号，用于确保数据完整性
        self._last_audio_sequence: int = 0  # 上次收到的音频序列号
        self._audio_loss_count: int = 0  # 音频丢失计数
        self._audio_recovery_buffer: List[bytes] = []  # 音频恢复缓冲区
        self._listen_stop_received: bool = False  # 是否收到ListenMessage(stop)
        self._processing_start_time: float = 0.0  # 处理开始时间
        # 性能监控
        self._performance_metrics = {
            'total_audio_processing_time': 0.0,
            'total_vad_time': 0.0,
            'total_decode_time': 0.0,
            'total_resample_time': 0.0,
            'total_asr_time': 0.0,
            'total_streaming_asr_time': 0.0,
            'total_response_time': 0.0,  # 从语音结束到ASR结果的总响应时间
            'audio_packets_processed': 0,
            'vad_calls': 0,
            'asr_calls': 0,
            'streaming_asr_calls': 0,
            'audio_losses': 0,
            'asr_results': 0,
            'streaming_asr_results': 0,
            'speech_segments': 0,  # 语音段数量
            'silence_detections': 0,  # 静音检测次数
        }
        # 流式处理状态
        self._streaming_asr_active: bool = False
        self._streaming_asr_buffer: bytes = b""
        self._last_streaming_result: str = ""
        # VAD状态
        self._is_speaking: bool = False
        self._silence_start_time: float = 0.0
        self._silence_frames: int = 0

    async def start_heartbeat(self):
        """启动心跳任务"""
        if self._ping_task is not None and not self._ping_task.done():
            return
        
        async def heartbeat_task():
            while self.websocket:
                try:
                    # 发送ping消息
                    ping_msg = {
                        "type": "ping",
                        "timestamp": time.time(),
                        "session_id": self.session_id
                    }
                    await self.websocket.send(json.dumps(ping_msg))
                    self._last_ping_time = time.time()
                    self.update_activity()
                    await asyncio.sleep(30)  # 每30秒发送一次
                except Exception as e:
                    self.logger.debug(f"Heartbeat error: {e}")
                    break
        
        self._ping_task = asyncio.create_task(heartbeat_task())

    def stop_heartbeat(self):
        """停止心跳任务"""
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            self._ping_task = None

    def update_activity(self) -> None:
        self.last_activity = time.time()

    def is_idle(self, timeout_seconds: float = 300.0) -> bool:
        return time.time() - self.last_activity > timeout_seconds


class ESP32Channel(BaseChannel):
    """ESP32 Channel: WebSocket server for ESP32 devices.

    Protocol flow:
    1. ESP32 connects via WebSocket
    2. ESP32 sends HELLO with device_id
    3. ESP32 sends AUDIO or TEXT messages
    4. Channel processes audio via VoiceProcessor (VAD+ASR)
    5. Text is sent to Agent for processing
    6. Agent response is converted to speech via TTS
    7. Audio is streamed back to ESP32
    """

    channel = "esp32"
    uses_manager_queue = False

    def __init__(
        self,
        process: ProcessHandler,
        enabled: bool = True,
        host: str = "0.0.0.0",
        port: int = 8080,
        auth_enabled: bool = False,
        auth_key: str = "",
        allowed_devices: Optional[List[str]] = None,
        voice_config: Optional[Dict[str, Any]] = None,
        log_level: str = "info",
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
        filter_tool_messages: bool = False,
        filter_thinking: bool = False,
        dm_policy: str = "open",
        group_policy: str = "open",
        allow_from: Optional[List[str]] = None,
        deny_message: str = "",
        require_mention: bool = False,
    ):
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
            dm_policy=dm_policy,
            group_policy=group_policy,
            allow_from=allow_from,
            deny_message=deny_message,
            require_mention=require_mention,
        )
        self.enabled = enabled
        self.host = host
        self.port = port
        self.auth_enabled = auth_enabled
        self.auth_key = auth_key
        self.allowed_devices: Set[str] = set(allowed_devices or [])
        self.voice_config = voice_config or {}
        self.log_level = log_level
        
        # 配置ESP32通道的日志级别
        import logging
        self.logger = logging.getLogger(__name__)
        level_map = {
            "critical": logging.CRITICAL,
            "error": logging.ERROR,
            "warning": logging.WARNING,
            "info": logging.INFO,
            "debug": logging.DEBUG,
        }
        self.logger.setLevel(level_map.get(log_level.lower(), logging.INFO))

        self._server: Optional[websockets.Serve] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._protocol = XiaozhiProtocol()

        self._connections: Dict[str, ESP32DeviceConnection] = {}
        self._connections_lock = asyncio.Lock()

        self._voice_processor_config = self._build_voice_config()

    def _build_voice_config(self) -> VoiceProcessorConfig:
        return VoiceProcessorConfig(
            vad_type=self.voice_config.get("vad_type", "silero"),
            vad_threshold=self.voice_config.get("vad_threshold", 0.5),
            asr_type="funasr",
            asr_model="paraformer-zh",  # 🔧 关键修复：使用纯中文 Paraformer 模型，避免 SenseVoice 多语言混合问题
            tts_type=self.voice_config.get("tts_type", "edge"),
            tts_voice=self.voice_config.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
            sample_rate=self.voice_config.get("sample_rate", 16000),
            min_speech_duration_ms=self.voice_config.get("min_speech_duration_ms", 500),
            min_silence_duration_ms=self.voice_config.get("min_silence_duration_ms", 300),
        )

    @classmethod
    def from_env(
        cls,
        process: ProcessHandler,
        on_reply_sent: OnReplySent = None,
    ) -> "ESP32Channel":
        allowed_devices_env = os.getenv("ESP32_ALLOWED_DEVICES", "")
        allowed_devices = [
            s.strip()
            for s in allowed_devices_env.split(",")
            if s.strip()
        ] if allowed_devices_env else None

        voice_config = {
            "vad_type": os.getenv("ESP32_VAD_TYPE", "silero"),
            "vad_threshold": float(os.getenv("ESP32_VAD_THRESHOLD", "0.5")),
            "asr_type": os.getenv("ESP32_ASR_TYPE", "funasr"),
            "asr_model": os.getenv("ESP32_ASR_MODEL", "paraformer-zh"),  # 🔧 关键修复：使用纯中文 Paraformer 模型
            "tts_type": os.getenv("ESP32_TTS_TYPE", "edge"),
            "tts_voice": os.getenv("ESP32_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            "sample_rate": int(os.getenv("ESP32_SAMPLE_RATE", "16000")),
        }

        return cls(
            process=process,
            enabled=os.getenv("ESP32_CHANNEL_ENABLED", "1") == "1",
            host=os.getenv("ESP32_HOST", "0.0.0.0"),
            port=int(os.getenv("ESP32_PORT", "8080")),
            auth_enabled=os.getenv("ESP32_AUTH_ENABLED", "0") == "1",
            auth_key=os.getenv("ESP32_AUTH_KEY", ""),
            allowed_devices=allowed_devices,
            voice_config=voice_config,
            log_level=os.getenv("ESP32_LOG_LEVEL", "info"),
            on_reply_sent=on_reply_sent,
            dm_policy=os.getenv("ESP32_DM_POLICY", "open"),
            group_policy=os.getenv("ESP32_GROUP_POLICY", "open"),
            allow_from=allowed_devices,
            deny_message=os.getenv("ESP32_DENY_MESSAGE", ""),
            require_mention=os.getenv("ESP32_REQUIRE_MENTION", "0") == "1",
        )

    @classmethod
    def from_config(
        cls,
        process: ProcessHandler,
        config: Any,
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
        filter_tool_messages: bool = False,
        filter_thinking: bool = False,
        workspace_dir: Optional[Path] = None,
    ) -> "ESP32Channel":
        del workspace_dir
        return cls(
            process=process,
            enabled=getattr(config, "enabled", True),
            host=getattr(config, "host", "0.0.0.0"),
            port=getattr(config, "port", 8080),
            auth_enabled=getattr(config, "auth_enabled", False),
            auth_key=getattr(config, "auth_key", ""),
            allowed_devices=list(getattr(config, "allowed_devices", []) or []),
            voice_config=dict(getattr(config, "voice", {}) or {}),
            log_level=getattr(config, "log_level", "info"),
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
            dm_policy=getattr(config, "dm_policy", "open"),
            group_policy=getattr(config, "group_policy", "open"),
            allow_from=list(getattr(config, "allow_from", []) or []),
            deny_message=getattr(config, "deny_message", ""),
            require_mention=getattr(config, "require_mention", False),
        )

    def resolve_session_id(
        self,
        sender_id: str,
        channel_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        return f"esp32:{sender_id}"

    def build_agent_request_from_native(
        self,
        native_payload: Any,
    ) -> "AgentRequest":
        payload = native_payload if isinstance(native_payload, dict) else {}
        channel_id = payload.get("channel_id") or self.channel
        sender_id = payload.get("sender_id") or ""
        content_parts = payload.get("content_parts") or []
        meta = dict(payload.get("meta") or {})
        session_id = self.resolve_session_id(sender_id, meta)
        return self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )

    async def start(self) -> None:
        if not self.enabled:
            self.logger.debug("ESP32 channel disabled")
            return

        self._loop = asyncio.get_running_loop()

        self.logger.info(f"[ESP32] 🚀 Starting ESP32 channel on {self.host}:{self.port}")
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=20,  # 每20秒发送ping
            ping_timeout=10,   # 10秒超时
            close_timeout=5    # 关闭连接时最多等待5秒
        )
        self.logger.info(f"[ESP32] ✅ ESP32 channel started on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        async with self._connections_lock:
            for conn in self._connections.values():
                try:
                    await conn.websocket.close()
                except Exception:
                    pass
            self._connections.clear()

    async def _process_request(
        self,
        path: str,
        request: websockets.Request,
    ) -> Optional[websockets.Response]:
        try:
            # 获取请求头部
            connection_header = request.headers.get("connection", "").lower()
            upgrade_header = request.headers.get("upgrade", "").lower()
            if "upgrade" in connection_header and upgrade_header == "websocket":
                return None
            # 对于非 WebSocket 请求，返回 200 OK
            from websockets.http import Response
            return Response(200, "ESP32 Channel is running\n")
        except Exception as e:
            self.logger.error(f"Error in process_request: {e}")
            from websockets.http import Response
            return Response(500, "Internal Server Error\n")

    async def _handle_connection(
        self,
        websocket,
    ) -> None:
        device_id = await self._authenticate(websocket)
        if not device_id:
            # 认证失败，直接返回，让连接自动关闭
            return

        conn = ESP32DeviceConnection(
            device_id=device_id,
            websocket=websocket,
            protocol=self._protocol,
            logger=self.logger,
        )

        async with self._connections_lock:
            self._connections[device_id] = conn

        self.logger.info(f"[ESP32] 🔗 ESP32 device connected: {device_id}")

        # 🔧 关键修复：新连接时确保音频缓冲区为空
        conn._audio_buffer.clear()
        self.logger.info(f"[ESP32] 🧹 Audio buffer cleared for new connection: {device_id}")

        # 启动心跳任务
        conn._ping_task = asyncio.create_task(self._ping_task_func(conn))

        try:
            async for message in websocket:
                conn.update_activity()
                conn._last_ping_time = time.time()
                await self._handle_message(conn, message)
        except websockets.ConnectionClosed:
            self.logger.info(f"[ESP32] 🚫 ESP32 device disconnected: {device_id}")
        except Exception:
            self.logger.exception(f"[ESP32] ❌ Error handling ESP32 connection: {device_id}")
        finally:
            # 取消心跳任务
            if conn._ping_task:
                conn._ping_task.cancel()
                try:
                    await conn._ping_task
                except asyncio.CancelledError:
                    pass
            # 🔧 关键修复：连接断开时清理音频缓冲区
            conn._audio_buffer.clear()
            self.logger.info(f"[ESP32] 🧹 Audio buffer cleared on connection close: {device_id}")
            async with self._connections_lock:
                self._connections.pop(device_id, None)

    async def _authenticate(
        self,
        websocket,
    ) -> Optional[str]:
        device_id = None
        
        try:
            # 尝试从 URL 查询参数中获取 device-id
            # 注意：websockets 库的不同版本可能有不同的属性名
            request_path = ""
            if hasattr(websocket, 'path'):
                request_path = websocket.path
            elif hasattr(websocket, 'request') and hasattr(websocket.request, 'path'):
                request_path = websocket.request.path
            
            self.logger.info(f"Request path: {request_path}")
            
            from urllib.parse import parse_qs, urlparse
            parsed_url = urlparse(request_path)
            query_params = parse_qs(parsed_url.query)
            self.logger.info(f"Query params: {query_params}")
            device_id = query_params.get("device-id", [None])[0]

            # 测试用：如果没有 device-id，使用带时间戳的唯一ID
            if not device_id:
                device_id = f"test-device-{int(time.time())}"
            self.logger.info(f"Device ID: {device_id}")

            # 检查设备是否在允许列表中
            if self.allowed_devices and device_id not in self.allowed_devices:
                self.logger.warning(f"Device {device_id} not in allowed list")
                return None

        except Exception as e:
            self.logger.error(f"Error in authentication: {e}")
            # 即使出错，也返回带时间戳的唯一ID以允许连接
            device_id = f"test-device-{int(time.time())}"
            self.logger.info(f"Using default device ID: {device_id}")

        return device_id

    def _verify_token(self, token: str, device_id: str) -> bool:
        if not self.auth_key:
            return True
        try:
            import jwt
            payload = jwt.decode(token, self.auth_key, algorithms=["HS256"])
            return payload.get("device_id") == device_id
        except Exception:
            return False

    async def _handle_message(
        self,
        conn: ESP32DeviceConnection,
        message: Any,
    ) -> None:
        # 记录接收到的消息类型和大小
        if isinstance(message, bytes):
            conn.logger.info(f"[ESP32] 📥 Received binary data from {conn.device_id}: {len(message)} bytes")
        else:
            conn.logger.debug(f"[ESP32] 📥 Received text message from {conn.device_id}: {message[:200]}{'...' if len(str(message)) > 200 else ''}")
        
        parsed = self._protocol.parse(message)
        if parsed is None:
            conn.logger.warning(f"[ESP32] ⚠️ Failed to parse message from {conn.device_id}")
            return

        conn.logger.info(f"[ESP32] 📋 Parsed message type: {type(parsed).__name__}")
        if isinstance(parsed, HelloMessage):
            await self._handle_hello_obj(conn, parsed)
        elif isinstance(parsed, AudioMessage):
            conn.logger.info(f"Processing audio message: {len(parsed.data)} bytes")
            await self._handle_audio_obj(conn, parsed)
        elif isinstance(parsed, TextMessage):
            await self._handle_text_obj(conn, parsed)
        elif isinstance(parsed, ListenMessage):
            await self._handle_listen_obj(conn, parsed)
        elif isinstance(parsed, TTSMessage):
            await self._handle_tts_obj(conn, parsed)
        elif isinstance(parsed, STTMessage):
            await self._handle_stt_obj(conn, parsed)
        elif isinstance(parsed, LLMMessage):
            await self._handle_llm_obj(conn, parsed)
        elif isinstance(parsed, dict):
            msg_type = parsed.get("type")
            conn.logger.debug(f"Received dict message type: {msg_type}")
            if msg_type == MessageType.PING.value:
                await self._handle_ping(conn, parsed)
            elif msg_type == MessageType.ABORT.value:
                await self._handle_abort(conn)
            elif msg_type == MessageType.HELLO.value:
                await self._handle_hello(conn, parsed)
            elif msg_type == MessageType.AUDIO.value:
                await self._handle_audio(conn, parsed)
            elif msg_type == MessageType.TEXT.value:
                await self._handle_text(conn, parsed)
            else:
                conn.logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_hello_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: HelloMessage,
    ) -> None:
        conn.client_id = msg.client_id
        conn.version = msg.version
        conn.audio_config = msg.audio_config or {}

        # 不要重置voice_processor，避免每次都重新加载模型

        # 生成 session_id
        session_id = f"esp32:{conn.device_id}"
        conn.session_id = session_id
        
        # 标记连接已建立
        conn._connection_established = True
        
        # 记录设备能力和配置
        conn.logger.debug(f"[ESP32] Device capabilities: version={msg.version}, features={getattr(msg, 'features', {})}")
        conn.logger.debug(f"[ESP32] Audio configuration: {msg.audio_config}")
        
        # 根据文档规范，返回符合格式的 hello 响应
        response = self._protocol.encode_hello(
            device_id="copaw-server",
            client_id="copaw",
            version="1",
            features={"mcp": True},
            transport="websocket",
            audio_params={
                "format": "opus",
                "sample_rate": 16000,  # ✅ 使用 16kHz（ESP32 实际发送的）
                "channels": 1,
                "frame_duration": 60,
            },
            session_id=session_id,
        )
        await conn.websocket.send(response)
        conn.logger.info(f"[ESP32] 👋 HELLO processed for {conn.device_id}")

    async def _handle_audio_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: AudioMessage,
    ) -> None:
        start_time = time.time()
        conn.logger.info(f"Handling audio from {conn.device_id}: format={msg.format}, sample_rate={msg.sample_rate}, channels={msg.channels}, data_len={len(msg.data)}")
        
        # 增加音频数据包计数
        conn._performance_metrics['audio_packets_processed'] += 1
        
        if conn.voice_processor is None:
            conn.logger.info(f"Initializing voice processor for {conn.device_id}")
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()

        if conn.state_machine.is_speaking():
            conn.logger.info(f"Device is speaking, aborting for {conn.device_id}")
            await self._handle_abort(conn)
            return

        # 🔍 关键修改：检测是否是新对话的开始（从 IDLE 状态进入 LISTENING）
        is_new_session = conn.state_machine.is_idle()
        if is_new_session:
            conn.logger.info(f"🎤 NEW SESSION DETECTED: Starting fresh audio capture for {conn.device_id}")
            conn._audio_buffer.clear()  # 清空旧缓冲区
            conn._wake_word_detected = False  # 标记唤醒词还未检测
            conn._audio_sequence = 0  # 重置音频序列号
            conn._last_audio_sequence = 0  # 重置上次音频序列号
            conn._audio_loss_count = 0  # 重置音频丢失计数
            # 初始化VAD状态
            conn._is_speaking = False
            conn._silence_start_time = 0.0
            conn._silence_frames = 0
            # 初始化流式处理状态
            conn._streaming_asr_active = False
            conn._streaming_asr_buffer = b""
            conn._last_streaming_result = ""
            # 输出性能统计
            self._log_performance_metrics(conn)
        
        conn.state_machine.transition_to(DeviceState.LISTENING)
        
        # 增加音频序列号
        conn._audio_sequence += 1
        current_timestamp = time.time()
        
        # 🔍 音频数据完整性检查
        if conn._last_audio_sequence > 0:
            expected_sequence = conn._last_audio_sequence + 1
            if conn._audio_sequence != expected_sequence:
                loss_count = abs(conn._audio_sequence - expected_sequence)
                conn._audio_loss_count += loss_count
                conn._performance_metrics['audio_losses'] += loss_count
                conn.logger.warning(f"🚨 AUDIO DATA LOSS DETECTED: Expected seq={expected_sequence}, got seq={conn._audio_sequence}, lost {loss_count} packets, total loss={conn._audio_loss_count}")
        conn._last_audio_sequence = conn._audio_sequence
        
        # 🔍 关键调试：先记录原始 Opus 数据
        conn.logger.debug(f"[OPUS] Seq={conn._audio_sequence}, Raw data: {len(msg.data)} bytes, hex={msg.data[:32].hex()}...")
        
        # 处理 Opus 编码的音频
        audio_data = msg.data
        
        # 🔍 音频数据完整性检查
        if not audio_data:
            conn.logger.warning(f"🚨 EMPTY AUDIO DATA RECEIVED: Seq={conn._audio_sequence}")
            return
        
        if len(audio_data) < 2:
            conn.logger.warning(f"🚨 INVALID AUDIO DATA: Too short ({len(audio_data)} bytes), Seq={conn._audio_sequence}")
            return
        
        # 解码 Opus 到 PCM
        decode_time = 0.0
        if msg.format == AudioFormat.OPUS:
            try:
                # 初始化 Opus 解码器（如果需要）
                if conn._opus_decoder is None:
                    conn.logger.info(f"Initializing Opus decoder for {conn.device_id}: sample_rate={msg.sample_rate}, channels={msg.channels}")
                    conn._opus_decoder = OpusDecoder(
                        sample_rate=msg.sample_rate,
                        channels=msg.channels,
                    )
                # 解码 Opus 到 PCM
                start_decode_time = time.time()
                decoded_data = conn._opus_decoder.decode(audio_data)
                decode_time = time.time() - start_decode_time
                conn._performance_metrics['total_decode_time'] += decode_time
                conn.logger.debug(f"Decoded to PCM: {len(decoded_data)} bytes (took {decode_time:.3f}s)")
                audio_data = decoded_data
                
                # 🔍 关键调试：检查解码后的 PCM 数据
                import numpy as np
                pcm_array = np.frombuffer(audio_data, dtype=np.int16)
                
                # 🔍 PCM 数据完整性检查
                if len(pcm_array) == 0:
                    conn.logger.warning(f"🚨 EMPTY PCM DATA AFTER DECODE: Seq={conn._audio_sequence}")
                    return
                
            except OpusCodecError as e:
                conn.logger.error(f"Opus decode error: {e}")
                return
        
        # 🔍 关键修复：ESP32 已经发送 16kHz 音频，不需要重采样！
        conn.logger.debug(f"[SAMPLE RATE] ESP32 sends {msg.sample_rate}Hz, ASR expects 16000Hz")
        
        resample_time = 0.0
        if msg.sample_rate == 16000:
            resampled_audio = audio_data
            conn.logger.debug(f"[NO RESAMPLE] Using decoded PCM directly: {len(resampled_audio)} bytes")
        else:
            start_resample_time = time.time()
            resampled_audio = resample_audio(audio_data, msg.sample_rate, 16000)
            resample_time = time.time() - start_resample_time
            conn._performance_metrics['total_resample_time'] += resample_time
            conn.logger.debug(f"[RESAMPLED] {msg.sample_rate}Hz -> 16000Hz: {len(audio_data)} -> {len(resampled_audio)} bytes (took {resample_time:.3f}s)")
        
        # 🔍 实时VAD检测
        is_speech = False
        vad_time = 0.0
        if conn.voice_processor and hasattr(conn.voice_processor, '_vad') and conn.voice_processor._vad:
            try:
                start_vad_time = time.time()
                vad_result = conn.voice_processor._vad.is_speech(resampled_audio)
                vad_time = time.time() - start_vad_time
                conn._performance_metrics['total_vad_time'] += vad_time
                conn._performance_metrics['vad_calls'] += 1
                is_speech = vad_result.is_speech
                conn.logger.info(f"[VAD] Speech: {is_speech}, Probability: {vad_result.probability:.2f} (took {vad_time:.3f}s)")
            except Exception as e:
                conn.logger.warning(f"VAD error: {e}")
        
        # 🔍 语音活动检测逻辑
        if is_speech:
            # 检测到语音
            conn._is_speaking = True
            conn._silence_start_time = 0.0
            conn._silence_frames = 0
            conn.logger.info(f"[VAD] Speech detected, continuing to buffer")
            
            # 累积音频数据到智能缓冲区
            conn._audio_buffer.add(resampled_audio, current_timestamp)
            
            # 累积到流式ASR缓冲区
            conn._streaming_asr_buffer += resampled_audio
            
            # 当流式缓冲区达到一定大小（约0.5秒）时，开始流式ASR处理
            if len(conn._streaming_asr_buffer) >= 16000:  # 16kHz * 2 bytes * 0.5 second
                conn.logger.debug(f"[STREAMING ASR] Buffer size: {len(conn._streaming_asr_buffer)} bytes, starting streaming ASR")
                
                # 实时流式ASR处理
                if conn.voice_processor and hasattr(conn.voice_processor._asr, 'transcribe'):
                    try:
                        start_streaming_time = time.time()
                        # 使用当前缓冲区的最后2秒数据进行流式ASR
                        streaming_buffer = conn._streaming_asr_buffer[-64000:]  # 保留最后2秒数据
                        result = await conn.voice_processor._asr.transcribe(streaming_buffer, 16000)
                        streaming_time = time.time() - start_streaming_time
                        conn._performance_metrics['total_streaming_asr_time'] += streaming_time
                        conn._performance_metrics['streaming_asr_calls'] += 1
                        conn.logger.debug(f"[STREAMING ASR] Result: '{result.text}' (took {streaming_time:.3f}s)")
                        
                        # 如果结果与上次不同，发送中间结果
                        if result.text and result.text != conn._last_streaming_result:
                            conn._last_streaming_result = result.text
                            conn._performance_metrics['streaming_asr_results'] += 1
                            # 发送中间STT消息给设备
                            stt_msg = self._protocol.encode_stt(
                                text=result.text,
                                session_id=conn.session_id,
                                is_final=False
                            )
                            await conn.websocket.send(stt_msg)
                            conn.logger.info(f"[STREAMING STT SENT] Device: {conn.device_id}, Text: {result.text}")
                    except Exception as e:
                        conn.logger.warning(f"Streaming ASR error: {e}")
        else:
            # 检测到静音
            if conn._is_speaking:
                # 开始静音
                if conn._silence_start_time == 0.0:
                    conn._silence_start_time = current_timestamp
                    conn._silence_frames = 1
                    conn.logger.info(f"[VAD] Silence started at {conn._silence_start_time}")
                else:
                    # 计算静音持续时间
                    silence_duration = current_timestamp - conn._silence_start_time
                    conn._silence_frames += 1
                    conn.logger.info(f"[VAD] Silence duration: {silence_duration:.2f}s, frames: {conn._silence_frames}")
                    
                    # 如果静音持续时间超过阈值（200ms），触发ASR处理
                    if silence_duration >= 0.2:
                        conn.logger.info(f"[VAD] Silence threshold reached ({silence_duration:.2f}s), processing ASR")
                        await self._process_audio_buffer(conn)
                        conn._is_speaking = False
                        conn._silence_start_time = 0.0
                        conn._silence_frames = 0
                        conn._streaming_asr_buffer = b""
                        conn._last_streaming_result = ""
            else:
                # 持续静音，不累积到缓冲区
                conn.logger.debug(f"[VAD] Continued silence, not buffering")
                return
        
        # 🔍 详细日志：音频缓冲区状态
        total_bytes = conn._audio_buffer.size()
        total_samples = total_bytes // 2  # 16-bit = 2 bytes per sample
        total_duration = total_samples / 16000  # seconds
        conn.logger.debug(f"[BUFFER STATUS] Chunks: {conn._audio_buffer.count()}, Size: {total_bytes} bytes, Duration: {total_duration:.3f}s, Loss Count: {conn._audio_loss_count}, Speaking: {conn._is_speaking}")
        
        # 🔧 关键修复: 动态缓冲区管理策略
        # 根据语音活动状态和缓冲区大小动态调整
        if conn._is_speaking:
            # 说话时，使用较大的缓冲区（最多10秒）
            max_duration = 10.0
            max_chunks = 150
        else:
            # 静音时，使用较小的缓冲区（最多2秒）
            max_duration = 2.0
            max_chunks = 30
        
        # 检查缓冲区大小是否超过限制
        if total_duration >= max_duration or conn._audio_buffer.count() >= max_chunks or total_bytes >= 1024 * 1024:
            # 缓冲区已满，强制处理（防止内存溢出）
            conn.logger.warning(f"⚠️ Audio buffer limit reached (duration: {total_duration:.2f}s, chunks: {conn._audio_buffer.count()}), forcing ASR processing")
            await self._process_audio_buffer(conn)
        
        # 🔍 处理时间统计
        total_process_time = time.time() - start_time
        conn._performance_metrics['total_audio_processing_time'] += total_process_time
        conn.logger.debug(f"[PROCESS TIME] Total: {total_process_time:.3f}s, VAD: {vad_time:.3f}s, Decode: {decode_time:.3f}s, Resample: {resample_time:.3f}s")
    
    def _log_performance_metrics(self, conn: ESP32DeviceConnection):
        """输出性能统计信息"""
        metrics = conn._performance_metrics
        if metrics['audio_packets_processed'] > 0:
            avg_processing_time = metrics['total_audio_processing_time'] / metrics['audio_packets_processed'] if metrics['audio_packets_processed'] > 0 else 0
            avg_vad_time = metrics['total_vad_time'] / metrics['vad_calls'] if metrics['vad_calls'] > 0 else 0
            avg_decode_time = metrics['total_decode_time'] / metrics['audio_packets_processed'] if metrics['audio_packets_processed'] > 0 else 0
            avg_asr_time = metrics['total_asr_time'] / metrics['asr_calls'] if metrics['asr_calls'] > 0 else 0
            avg_streaming_asr_time = metrics['total_streaming_asr_time'] / metrics['streaming_asr_calls'] if metrics['streaming_asr_calls'] > 0 else 0
            
            conn.logger.info(f"[PERFORMANCE METRICS] Device: {conn.device_id}")
            conn.logger.info(f"[PERFORMANCE METRICS] Audio packets: {metrics['audio_packets_processed']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Audio losses: {metrics['audio_losses']}")
            conn.logger.info(f"[PERFORMANCE METRICS] VAD calls: {metrics['vad_calls']}")
            conn.logger.info(f"[PERFORMANCE METRICS] ASR calls: {metrics['asr_calls']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Streaming ASR calls: {metrics['streaming_asr_calls']}")
            conn.logger.info(f"[PERFORMANCE METRICS] ASR results: {metrics['asr_results']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Streaming ASR results: {metrics['streaming_asr_results']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Speech segments: {metrics['speech_segments']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Silence detections: {metrics['silence_detections']}")
            conn.logger.info(f"[PERFORMANCE METRICS] Avg processing time: {avg_processing_time:.3f}s")
            conn.logger.info(f"[PERFORMANCE METRICS] Avg VAD time: {avg_vad_time:.3f}s")
            conn.logger.info(f"[PERFORMANCE METRICS] Avg decode time: {avg_decode_time:.3f}s")
            conn.logger.info(f"[PERFORMANCE METRICS] Avg ASR time: {avg_asr_time:.3f}s")
            conn.logger.info(f"[PERFORMANCE METRICS] Avg streaming ASR time: {avg_streaming_asr_time:.3f}s")
            # 计算平均响应时间
            avg_response_time = metrics['total_response_time'] / metrics['silence_detections'] if metrics['silence_detections'] > 0 else 0
            conn.logger.info(f"[PERFORMANCE METRICS] Avg response time (speech end to ASR): {avg_response_time:.3f}s")
            
            # 重置性能指标
            conn._performance_metrics = {
                'total_audio_processing_time': 0.0,
                'total_vad_time': 0.0,
                'total_decode_time': 0.0,
                'total_resample_time': 0.0,
                'total_asr_time': 0.0,
                'total_streaming_asr_time': 0.0,
                'total_response_time': 0.0,
                'audio_packets_processed': 0,
                'vad_calls': 0,
                'asr_calls': 0,
                'streaming_asr_calls': 0,
                'audio_losses': 0,
                'asr_results': 0,
                'streaming_asr_results': 0,
                'speech_segments': 0,
                'silence_detections': 0,
            }

    async def _process_audio_buffer(self, conn: ESP32DeviceConnection) -> None:
        """处理音频缓冲区中的数据"""
        if conn._audio_buffer.count() == 0:
            conn.logger.debug(f"Audio buffer is empty for {conn.device_id}")
            return
        
        combined_audio = conn._audio_buffer.get_all()
        total_bytes = len(combined_audio)
        
        conn.logger.info(f"🎵 Processing audio buffer: {conn._audio_buffer.count()} chunks, {total_bytes} bytes")
        conn._performance_metrics['speech_segments'] += 1
        
        # 音频质量检查
        combined_array = np.frombuffer(combined_audio, dtype=np.int16)
        combined_dynamic_range = combined_array.max() - combined_array.min()
        combined_rms = np.sqrt(np.mean(combined_array.astype(float)**2))
        
        # 质量门控：动态范围 < 100 或 RMS < 10 则丢弃
        if combined_dynamic_range < 100 or combined_rms < 10:
            conn.logger.warning(f"⚠️ Audio quality too poor (DR={combined_dynamic_range}, RMS={combined_rms:.1f}), skipping ASR")
            conn._audio_buffer.clear()
            # 重置VAD状态
            conn._is_speaking = False
            conn._silence_start_time = 0.0
            conn._silence_frames = 0
            return
        
        conn.logger.info(f"📊 Audio quality: DR={combined_dynamic_range}, RMS={combined_rms:.1f} - Processing ASR")
        
        # 处理合并后的音频数据
        try:
            # 记录响应时间开始（从语音结束到ASR结果）
            response_start_time = time.time()
            start_time = time.time()
            # 使用异步处理，避免阻塞主事件循环
            text = await asyncio.create_task(conn.voice_processor.process_audio(combined_audio, 16000))
            process_time = time.time() - start_time
            # 记录响应时间结束
            response_time = time.time() - response_start_time
            conn._performance_metrics['total_asr_time'] += process_time
            conn._performance_metrics['total_response_time'] += response_time
            conn._performance_metrics['asr_calls'] += 1
            conn._performance_metrics['silence_detections'] += 1
            
            conn.logger.info(f"📝 ASR result: '{text}' (took {process_time:.2f}s)")
            conn.logger.info(f"[RESPONSE TIME] Speech end to ASR result: {response_time:.3f}s")
            
            if text:
                # 发送 STT 消息给设备
                stt_msg = self._protocol.encode_stt(
                    text=text,
                    session_id=conn.session_id,
                    is_final=True
                )
                await conn.websocket.send(stt_msg)
                conn.logger.info(f"[STT SENT] Device: {conn.device_id}, Text: {text}")
                conn._performance_metrics['asr_results'] += 1
                
                # 处理用户文本
                await self._process_user_text(conn, text)
            else:
                conn.logger.warning(f"[ASR EMPTY] No valid text returned for {conn.device_id} (filtered or empty)")
                # 🔧 关键修复：ASR结果被过滤时，清空缓冲区但不处理
                conn._audio_buffer.clear()
                conn.logger.info(f"[BUFFER CLEARED] Device: {conn.device_id} (filtered ASR result)")
        except Exception as e:
            conn.logger.error(f"Error processing audio for {conn.device_id}: {e}")
            # 异常时清空缓冲区
            conn._audio_buffer.clear()
            conn.logger.info(f"[BUFFER CLEARED] Device: {conn.device_id} (error)")
        finally:
            # 重置VAD状态，为下一次语音检测做准备
            conn._is_speaking = False
            conn._silence_start_time = 0.0
            conn._silence_frames = 0
            # 清空缓冲区
            conn._audio_buffer.clear()
            conn.logger.info(f"[BUFFER CLEARED] Device: {conn.device_id} (processing completed)")

    async def _handle_text_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: TextMessage,
    ) -> None:
        if msg.type == MessageType.START_LISTEN:
            conn.state_machine.transition_to(DeviceState.LISTENING)
            if conn.voice_processor:
                conn.voice_processor.reset()
            return
        elif msg.type == MessageType.STOP_LISTEN:
            conn.state_machine.transition_to(DeviceState.IDLE)
            return
        elif msg.type == MessageType.ABORT:
            await self._handle_abort(conn)
            return

        if msg.text:
            await self._process_user_text(conn, msg.text)

    async def _handle_listen_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: ListenMessage,
    ) -> None:
        if msg.state == ListenState.START:
            conn.state_machine.transition_to(DeviceState.LISTENING)
            if conn.voice_processor:
                conn.voice_processor.reset()
            conn.logger.info(f"LISTEN start for {conn.device_id}")
        elif msg.state == ListenState.STOP:
            conn.logger.info(f"⏹️ LISTEN stop received for {conn.device_id}")
            # 🔧 关键修复：设置停止标志
            conn._listen_stop_received = True
            # 🔧 关键修复：收到stop时触发ASR处理
            if hasattr(conn, '_audio_buffer') and conn._audio_buffer.count() > 0:
                conn.logger.info(f"🎵 Processing {conn._audio_buffer.count()} audio chunks on STOP")
                await self._process_audio_buffer(conn)
            # 注意：不要立即转到IDLE状态，等待TTS完成
            # conn.state_machine.transition_to(DeviceState.IDLE)
        elif msg.state == ListenState.DETECT:
            conn.logger.info(f"🎤 Wake word detected for {conn.device_id}: {msg.text}")
            
            # 记录唤醒词检测时间戳
            conn._wake_word_timestamp = time.time()
            conn._wake_word_detected = True
            
            # 🔧 关键修复：不清空缓冲区！因为 ESP32 先发送音频包，后发送唤醒词通知
            # 缓冲区中可能包含唤醒词+指令的完整音频，清空会导致语音丢失
            # 保留缓冲区，让后续音频继续累积，确保捕获完整指令
            if hasattr(conn, '_audio_buffer') and conn._audio_buffer.size() > 0:
                conn.logger.info(f"� Keeping audio buffer with {conn._audio_buffer.count()} chunks, continuing to capture command audio")
            
            # 重置 VoiceProcessor，确保从唤醒词后开始重新识别
            if conn.voice_processor:
                conn.voice_processor.reset()
            
            # 根据协议规范，当检测到唤醒词时，返回 hello 响应
            # 生成 session_id
            session_id = f"esp32:{conn.device_id}"
            conn.session_id = session_id
            
            # 返回符合格式的 hello 响应
            response = self._protocol.encode_hello(
                device_id="copaw-server",
                client_id="copaw",
                version="1",
                features={"mcp": True},
                transport="websocket",
                audio_params={
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
                session_id=session_id,
            )
            await conn.websocket.send(response)
            conn.logger.info(f"[ESP32] 👋 HELLO processed for wake word: {msg.text}")

    async def _handle_tts_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: TTSMessage,
    ) -> None:
        if msg.state == TTSState.START:
            conn.logger.info(f"TTS start for {conn.device_id}")
        elif msg.state == TTSState.STOP:
            conn.logger.info(f"TTS stop for {conn.device_id}")
        elif msg.state == TTSState.SENTENCE_START:
            conn.logger.info(f"TTS sentence: {msg.text}")

    async def _handle_stt_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: STTMessage,
    ) -> None:
        conn.logger.info(f"STT result for {conn.device_id}: {msg.text} (final={msg.is_final})")

    async def _handle_llm_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: LLMMessage,
    ) -> None:
        conn.logger.info(f"LLM emotion for {conn.device_id}: {msg.emotion}, text: {msg.text}")

    async def _handle_hello(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        conn.client_id = msg.get("client_id", msg.get("client-id", ""))
        conn.version = msg.get("version", "")
        conn.audio_config = msg.get("audio_params", msg.get("audio_config", {}))

        # Don't initialize voice processor here, do it lazily when needed
        # 不要重置voice_processor，避免每次都重新加载模型

        # 生成 session_id
        session_id = f"esp32:{conn.device_id}"
        conn.session_id = session_id
        
        # 标记连接已建立
        conn._connection_established = True
        
        # 记录设备能力和配置
        conn.logger.info(f"Device capabilities: version={conn.version}, features={msg.get('features', {})}")
        conn.logger.info(f"Audio configuration: {conn.audio_config}")
        
        # 根据文档规范，返回符合格式的 hello 响应
        response = self._protocol.encode_hello(
            device_id="copaw-server",
            client_id="copaw",
            version="1",
            features={"mcp": True},
            transport="websocket",
            audio_params={
                "format": "opus",
                "sample_rate": 16000,  # 🔧 修复：改为 16kHz，与 ESP32 实际发送的一致
                "channels": 1,
                "frame_duration": 60,
            },
            session_id=session_id,
        )
        await conn.websocket.send(response)
        conn.logger.info(f"[ESP32] 👋 HELLO processed for {conn.device_id}")

    async def _handle_audio(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        if conn.voice_processor is None:
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()

        if conn.state_machine.is_speaking():
            await self._handle_abort(conn)

        conn.state_machine.transition_to(DeviceState.LISTENING)
        
        # 获取音频数据
        audio_data = msg.get("data", b"")
        if isinstance(audio_data, str):
            import base64
            audio_data = base64.b64decode(audio_data)
        
        audio_config = msg.get("audio_config", {})
        sample_rate = audio_config.get("sample_rate", 16000)
        current_timestamp = time.time()
        
        conn._audio_buffer.add(audio_data, current_timestamp)

        text = await conn.voice_processor.process_audio(audio_data, sample_rate)
        if text:
            await self._process_user_text(conn, text)

    async def _handle_text(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        msg_type = msg.get("type")
        if msg_type == MessageType.START_LISTEN.value:
            conn.state_machine.transition_to(DeviceState.LISTENING)
            if conn.voice_processor:
                conn.voice_processor.reset()
            return
        elif msg_type == MessageType.STOP_LISTEN.value:
            conn.state_machine.transition_to(DeviceState.IDLE)
            return
        elif msg_type == MessageType.ABORT.value:
            await self._handle_abort(conn)
            return

        text = msg.get("text", "")
        if text:
            await self._process_user_text(conn, text)

    async def _handle_abort(
        self,
        conn: ESP32DeviceConnection,
    ) -> None:
        conn._is_aborted = True
        if conn._speaking_task and not conn._speaking_task.done():
            conn._speaking_task.cancel()
            try:
                await conn._speaking_task
            except asyncio.CancelledError:
                pass
        conn.state_machine.transition_to(DeviceState.IDLE)
        conn._is_aborted = False

    async def close_audio_channel(
        self,
        device_id: str,
        send_goodbye: bool = False,
    ) -> None:
        """关闭音频通道，符合文档规范"""
        async with self._connections_lock:
            conn = self._connections.get(device_id)
        
        if conn:
            try:
                # 取消正在进行的任务
                if conn._speaking_task and not conn._speaking_task.done():
                    conn._speaking_task.cancel()
                    try:
                        await conn._speaking_task
                    except asyncio.CancelledError:
                        pass
                
                # 关闭 WebSocket 连接
                if conn.websocket:
                    try:
                        await conn.websocket.close()
                    except Exception:
                        pass
                
                # 清理连接
                async with self._connections_lock:
                    self._connections.pop(device_id, None)
                
                conn.logger.info(f"Audio channel closed for {device_id}")
            except Exception:
                conn.logger.exception(f"Error closing audio channel for {device_id}")

    async def _handle_ping(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        response = self._protocol.encode_pong(msg.get("timestamp", 0))
        await conn.websocket.send(response)

    async def _process_user_text(
        self,
        conn: ESP32DeviceConnection,
        text: str,
    ) -> None:
        conn.state_machine.transition_to(DeviceState.PROCESSING)
        conn.logger.info(f"Processing text from {conn.device_id}: {text}")
        
        # 🔧 关键修复：发送处理中状态通知
        try:
            processing_msg = {
                "type": "processing",
                "session_id": conn.session_id,
                "status": "processing",
                "message": "正在处理您的请求...",
                "timestamp": int(time.time())
            }
            await conn.websocket.send(json.dumps(processing_msg))
            conn.logger.info(f"Sent processing status to {conn.device_id}")
        except Exception as e:
            conn.logger.warning(f"Error sending processing status: {e}")

        payload = {
            "channel_id": self.channel,
            "sender_id": conn.device_id,
            "content_parts": [
                TextContent(type=ContentType.TEXT, text=text),
            ],
            "meta": {
                "device_id": conn.device_id,
                "client_id": conn.client_id,
            },
        }

        request = self.build_agent_request_from_native(payload)
        conn.logger.info(f"Built AgentRequest: session_id={request.session_id}, user_id={request.user_id}")

        try:
            response_text = ""
            event_count = 0
            async for event in self._process(request):
                event_count += 1
                obj = getattr(event, "object", None)
                status = getattr(event, "status", None)
                conn.logger.debug(f"Received event: object={obj}, status={status}")

                if obj == "message" and status:
                    from agentscope_runtime.engine.schemas.agent_schemas import RunStatus
                    if status == RunStatus.Completed:
                        conn.logger.info(f"Received completed message event")
                        parts = self._message_to_content_parts(event)
                        conn.logger.info(f"Message parts: {len(parts)} parts")
                        for p in parts:
                            part_type = getattr(p, "type", None)
                            part_text = getattr(p, "text", "") or getattr(p, "refusal", "") or ""
                            conn.logger.info(f"Part: type={part_type}, text={part_text}")
                            if part_type == ContentType.TEXT:
                                response_text += part_text
                            elif part_type == ContentType.REFUSAL:
                                response_text += part_text

            conn.logger.info(f"Processed {event_count} events, response_text length: {len(response_text)}")
            if response_text:
                conn.logger.info(f"Sending speech response: {response_text}")
                await self._send_speech(conn, response_text)
            else:
                conn.logger.warning(f"No response text to send for {conn.device_id}")

        except Exception:
            conn.logger.exception(f"Error processing text from {conn.device_id}")
            await self._send_error(conn, 500, "Processing error")
        finally:
            conn.state_machine.transition_to(DeviceState.IDLE)

    async def _send_speech(
        self,
        conn: ESP32DeviceConnection,
        text: str,
    ) -> None:
        conn.state_machine.transition_to(DeviceState.SPEAKING)
        conn.logger.info(f"Sending speech to {conn.device_id}: {text}")

        try:
            # 检查连接状态
            if not await self._check_connection(conn):
                conn.logger.warning(f"Connection closed, cannot send speech to {conn.device_id}")
                conn.state_machine.transition_to(DeviceState.IDLE)
                return

            # Initialize voice processor if needed
            if conn.voice_processor is None:
                conn.logger.info(f"Initializing voice processor for {conn.device_id}")
                conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
                await conn.voice_processor.initialize()

            # 发送 TTS start 消息
            conn.logger.info(f"Sending TTS start message to {conn.device_id}")
            tts_start = self._protocol.encode_tts_start(session_id=conn.session_id)
            if not await self._send_with_retry(conn, tts_start):
                conn.logger.warning(f"Failed to send TTS start message to {conn.device_id}")
                conn.state_machine.transition_to(DeviceState.IDLE)
                return
            
            # 发送句子开始消息
            conn.logger.info(f"Sending TTS sentence message to {conn.device_id}: {text}")
            tts_sentence = self._protocol.encode_tts_sentence(text, session_id=conn.session_id)
            if not await self._send_with_retry(conn, tts_sentence):
                conn.logger.warning(f"Failed to send TTS sentence message to {conn.device_id}")
                conn.state_machine.transition_to(DeviceState.IDLE)
                return

            # 预检查连接状态，避免合成后连接断开
            if not await self._check_connection(conn):
                conn.logger.warning(f"Connection closed before synthesizing speech for {conn.device_id}")
                conn.state_machine.transition_to(DeviceState.IDLE)
                return

            conn.logger.info(f"Synthesizing speech for {conn.device_id}")
            audio_data = await conn.voice_processor.synthesize_speech(text)
            conn.logger.info(f"Speech synthesized: {len(audio_data) if audio_data else 0} bytes")
            
            if audio_data and not conn._is_aborted:
                # 检查设备是否支持 Opus
                audio_format = AudioFormat.PCM
                if conn.audio_config.get("format") == "opus":
                    audio_format = AudioFormat.OPUS
                    # 编码为 Opus
                    try:
                        # 获取设备期望的采样率
                        target_sample_rate = conn.audio_config.get("sample_rate", 16000)
                        # 获取 TTS 生成的采样率
                        tts_sample_rate = conn.voice_processor.tts_sample_rate
                        
                        # 优化：只在必要时进行重采样
                        processed_audio = audio_data
                        if tts_sample_rate != target_sample_rate:
                            conn.logger.info(f"Resampling audio from {tts_sample_rate}Hz to {target_sample_rate}Hz")
                            processed_audio = resample_audio(audio_data, tts_sample_rate, target_sample_rate)
                            conn.logger.info(f"Resampled audio: {len(processed_audio)} bytes")
                        
                        if conn._opus_encoder is None:
                            conn.logger.info(f"Initializing Opus encoder for {conn.device_id}")
                            conn._opus_encoder = OpusEncoder(
                                sample_rate=target_sample_rate,
                                channels=conn.audio_config.get("channels", 1),
                            )
                        # 编码 PCM 到 Opus
                        conn.logger.info(f"Encoding PCM to Opus for {conn.device_id}")
                        # 优化：直接使用处理后的音频数据
                        opus_frames = conn._opus_encoder.encode_stream(processed_audio)
                        conn.logger.info(f"Encoded {len(opus_frames)} Opus frames")
                        
                        # 分块发送 Opus 帧，添加更健壮的错误处理
                        success_count = 0
                        total_frames = len(opus_frames)
                        for i, frame in enumerate(opus_frames):
                            if conn._is_aborted:
                                conn.logger.info(f"Aborted sending Opus frames to {conn.device_id}")
                                break
                            
                            # 每次发送前检查连接状态
                            if not await self._check_connection(conn):
                                conn.logger.warning(f"Connection closed while sending Opus frames to {conn.device_id}")
                                # 尝试重新连接
                                if await self._reconnect_if_needed(conn):
                                    conn.logger.info(f"Reconnected, continuing to send Opus frames")
                                else:
                                    break
                            
                            conn.logger.debug(f"Sending Opus frame {i+1}/{total_frames} to {conn.device_id}")
                            if await self._send_with_retry(conn, frame, max_retries=3):
                                success_count += 1
                                # 优化：使用更精确的延迟控制
                                # 根据帧大小和采样率计算实际需要的延迟
                                frame_duration = len(frame) * 8 / (target_sample_rate * 16 * conn.audio_config.get("channels", 1))
                                await asyncio.sleep(max(0.001, frame_duration))  # 最小1ms延迟
                            else:
                                conn.logger.warning(f"Failed to send Opus frame {i+1} to {conn.device_id}")
                                # 尝试重新连接后继续发送
                                if await self._reconnect_if_needed(conn):
                                    conn.logger.info(f"Reconnected, retrying Opus frame {i+1}")
                                    if await self._send_with_retry(conn, frame, max_retries=2):
                                        success_count += 1
                            
                        if success_count > 0:
                            conn.logger.info(f"Successfully sent {success_count}/{total_frames} Opus frames to {conn.device_id}")
                        else:
                            conn.logger.error(f"Failed to send any Opus frames to {conn.device_id}")
                            # 尝试回退到PCM
                            if await self._check_connection(conn):
                                conn.logger.info(f"Falling back to PCM for {conn.device_id}")
                                await self._send_with_retry(conn, audio_data)
                    except OpusCodecError as e:
                        conn.logger.error(f"Opus encode error: {e}")
                        # 回退到 PCM
                        conn.logger.info(f"Falling back to PCM for {conn.device_id}")
                        if await self._check_connection(conn):
                            await self._send_with_retry(conn, audio_data)
                else:
                    # 发送 PCM 音频，添加分块发送以提高可靠性
                    conn.logger.info(f"Sending PCM audio to {conn.device_id}: {len(audio_data)} bytes")
                    if await self._check_connection(conn):
                        # 分块发送 PCM 数据，每块 1024 字节
                        chunk_size = 1024
                        for i in range(0, len(audio_data), chunk_size):
                            if conn._is_aborted:
                                break
                            if not await self._check_connection(conn):
                                if await self._reconnect_if_needed(conn):
                                    conn.logger.info(f"Reconnected, continuing to send PCM audio")
                                else:
                                    break
                            chunk = audio_data[i:i+chunk_size]
                            if not await self._send_with_retry(conn, chunk, max_retries=3):
                                conn.logger.warning(f"Failed to send PCM chunk {i//chunk_size + 1}")
                            await asyncio.sleep(0.005)  # 5ms 延迟，避免设备处理不过来
            
            # 发送 TTS stop 消息
            if not conn._is_aborted and await self._check_connection(conn):
                conn.logger.info(f"Sending TTS stop message to {conn.device_id}")
                tts_stop = self._protocol.encode_tts_stop(session_id=conn.session_id)
                if not await self._send_with_retry(conn, tts_stop):
                    conn.logger.warning(f"Failed to send TTS stop message to {conn.device_id}")
                    # 尝试重新连接后发送
                    if await self._reconnect_if_needed(conn):
                        await self._send_with_retry(conn, tts_stop)
                
        except asyncio.CancelledError:
            conn.logger.info(f"Speech sending cancelled for {conn.device_id}")
            pass
        except Exception as e:
            conn.logger.exception(f"Error sending speech to {conn.device_id}: {e}")
            # Fallback to text on error
            try:
                if await self._check_connection(conn):
                    conn.logger.info(f"Falling back to text message for {conn.device_id}")
                    text_msg = self._protocol.encode_text(text)
                    await self._send_with_retry(conn, text_msg)
                else:
                    # 尝试重新连接后发送文本消息
                    if await self._reconnect_if_needed(conn):
                        conn.logger.info(f"Reconnected, sending fallback text message to {conn.device_id}")
                        text_msg = self._protocol.encode_text(text)
                        await self._send_with_retry(conn, text_msg)
            except Exception:
                conn.logger.exception(f"Error sending fallback text message to {conn.device_id}")
                pass
        finally:
            # 确保状态正确切换
            if not conn._is_aborted:
                # 🔧 关键修复：TTS完成后回到LISTENING状态，而不是IDLE
                # 这样ESP32保持在监听状态，等待下一次唤醒
                conn.state_machine.transition_to(DeviceState.LISTENING)
                conn.logger.info(f"TTS completed, device {conn.device_id} returned to LISTENING state")

    async def _check_connection(self, conn: ESP32DeviceConnection) -> bool:
        """检查 WebSocket 连接是否正常"""
        if not conn.websocket:
            conn.logger.warning(f"No websocket connection for {conn.device_id}")
            return False
        try:
            # 检查连接状态
            if hasattr(conn.websocket, 'open'):
                is_open = conn.websocket.open
                if not is_open:
                    conn.logger.warning(f"WebSocket connection closed for {conn.device_id}")
                    return False
            
            # 检查连接是否已建立（收到Hello消息）
            if not conn._connection_established:
                conn.logger.warning(f"Connection not established (Hello message not received) for {conn.device_id}")
                return False
            
            # 检查心跳状态（增加到120秒，给AI处理留出更多时间）
            if time.time() - conn._last_ping_time > 120:
                conn.logger.warning(f"No ping response for {conn.device_id} in 120 seconds")
                return False
            
            return True
        except Exception as e:
            conn.logger.exception(f"Error checking connection for {conn.device_id}: {e}")
            return False

    async def _reconnect_if_needed(self, conn: ESP32DeviceConnection) -> bool:
        """尝试重新连接ESP32设备"""
        try:
            conn.logger.info(f"Attempting to reconnect to {conn.device_id}")
            
            # 检查是否有重连机制
            # 注意：由于ESP32作为客户端连接到Paws服务器，
            # 服务器无法主动发起连接，只能等待ESP32重新连接
            # 因此这里主要是清理旧连接并准备接收新连接
            
            # 关闭旧连接
            if conn.websocket:
                try:
                    await conn.websocket.close()
                except Exception:
                    pass
                conn.websocket = None
            
            # 重置连接状态
            conn._connection_established = False
            conn._last_ping_time = time.time()
            
            # 等待一小段时间，让ESP32有机会重新连接
            await asyncio.sleep(0.5)
            
            # 检查是否已有新连接
            # 注意：在实际情况下，ESP32会主动重新连接，
            # 这里我们只能检查当前连接状态
            
            # 由于服务器无法主动连接ESP32，
            # 这里返回False，让调用方知道需要等待ESP32重新连接
            conn.logger.info(f"Reconnection attempt completed for {conn.device_id}")
            return False
        except Exception as e:
            conn.logger.exception(f"Error during reconnection attempt for {conn.device_id}: {e}")
            return False

    async def _send_with_retry(self, conn: ESP32DeviceConnection, data: str | bytes, max_retries: int = 3) -> bool:
        """发送消息并在失败时重试"""
        for attempt in range(max_retries):
            if not await self._check_connection(conn):
                conn.logger.warning(f"Connection closed, cannot send message to {conn.device_id}")
                return False
            try:
                # 检查数据类型并记录发送信息
                if isinstance(data, str):
                    # 对于文本消息，记录类型和长度
                    try:
                        parsed = json.loads(data)
                        msg_type = parsed.get('type', 'unknown')
                        conn.logger.debug(f"Sending {msg_type} message to {conn.device_id}, attempt {attempt+1}/{max_retries}")
                    except:
                        conn.logger.debug(f"Sending text message to {conn.device_id}, attempt {attempt+1}/{max_retries}")
                else:
                    # 对于二进制消息，记录大小
                    conn.logger.debug(f"Sending binary data ({len(data)} bytes) to {conn.device_id}, attempt {attempt+1}/{max_retries}")
                
                await conn.websocket.send(data)
                
                # 根据数据类型调整延迟
                if isinstance(data, bytes):
                    # 对于音频数据，使用更短的延迟
                    await asyncio.sleep(0.005)  # 5ms 延迟
                else:
                    # 对于文本消息，使用标准延迟
                    await asyncio.sleep(0.01)  # 10ms 延迟
                
                return True
            except Exception as e:
                conn.logger.warning(f"Attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    # 动态调整重试间隔，根据尝试次数增加
                    retry_delay = 0.1 * (attempt + 1)
                    conn.logger.debug(f"Waiting {retry_delay:.2f}s before next retry")
                    await asyncio.sleep(retry_delay)
                else:
                    conn.logger.error(f"All {max_retries} attempts failed to send message to {conn.device_id}")
                    return False

    async def _ping_task_func(self, conn: ESP32DeviceConnection) -> None:
        """心跳任务：定期发送ping消息并检查连接状态"""
        try:
            while True:
                await asyncio.sleep(30)  # 每30秒发送一次ping
                if not await self._check_connection(conn):
                    conn.logger.info(f"Connection closed for {conn.device_id}, stopping ping task")
                    break
                
                # 检查上次活动时间，超过2分钟无活动则断开连接
                if time.time() - conn.last_activity > 120:
                    conn.logger.info(f"Device {conn.device_id} inactive for 2 minutes, closing connection")
                    try:
                        await conn.websocket.close()
                    except Exception:
                        pass
                    break
                
                # 发送ping消息
                try:
                    ping_msg = self._protocol.encode_ping(int(time.time()))
                    await conn.websocket.send(ping_msg)
                    conn._last_ping_time = time.time()
                    conn.logger.debug(f"Sent ping to {conn.device_id}")
                except Exception as e:
                    conn.logger.warning(f"Error sending ping to {conn.device_id}: {e}")
                    break
        except asyncio.CancelledError:
            conn.logger.debug(f"Ping task cancelled for {conn.device_id}")
        except Exception as e:
            conn.logger.exception(f"Error in ping task for {conn.device_id}: {e}")

    async def _send_error(
        self,
        conn: ESP32DeviceConnection,
        code: int,
        message: str,
    ) -> None:
        error_msg = self._protocol.encode_error(code, message)
        await conn.websocket.send(error_msg)

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return

        device_id = to_handle
        if device_id.startswith("esp32:"):
            device_id = device_id[6:]

        async with self._connections_lock:
            conn = self._connections.get(device_id)

        if conn is None:
            conn.logger.warning(f"Device not connected: {device_id}")
            return

        await self._send_speech(conn, text)

    async def send_content_parts(
        self,
        to_handle: str,
        parts: List[OutgoingContentPart],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        text_parts = []
        for p in parts:
            t = getattr(p, "type", None)
            if t == ContentType.TEXT and getattr(p, "text", None):
                text_parts.append(p.text or "")
            elif t == ContentType.REFUSAL and getattr(p, "refusal", None):
                text_parts.append(p.refusal or "")

        body = "\n".join(text_parts) if text_parts else ""
        if body.strip():
            await self.send(to_handle, body.strip(), meta)
