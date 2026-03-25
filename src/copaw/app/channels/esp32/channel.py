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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

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

logger = logging.getLogger(__name__)


class ESP32DeviceConnection:
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
        self.audio_config: Dict[str, Any] = {}
        self.last_activity: float = time.time()
        self.voice_processor: Optional[VoiceProcessor] = None
        self._audio_buffer: List[bytes] = []
        self._is_aborted: bool = False
        self._speaking_task: Optional[asyncio.Task] = None
        self._opus_decoder: Optional[OpusDecoder] = None
        self._opus_encoder: Optional[OpusEncoder] = None

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
            asr_type=self.voice_config.get("asr_type", "funasr"),
            asr_model=self.voice_config.get("asr_model", "paraformer-zh"),
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
            "asr_model": os.getenv("ESP32_ASR_MODEL", "paraformer-zh"),
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
            logger.debug("ESP32 channel disabled")
            return

        self._loop = asyncio.get_running_loop()

        logger.info(f"Starting ESP32 channel on {self.host}:{self.port}")
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port
        )
        logger.info(f"ESP32 channel started on ws://{self.host}:{self.port}")

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
            logger.error(f"Error in process_request: {e}")
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
        )

        async with self._connections_lock:
            self._connections[device_id] = conn

        logger.info(f"ESP32 device connected: {device_id}")

        try:
            async for message in websocket:
                conn.update_activity()
                await self._handle_message(conn, message)
        except websockets.ConnectionClosed:
            logger.info(f"ESP32 device disconnected: {device_id}")
        except Exception:
            logger.exception(f"Error handling ESP32 connection: {device_id}")
        finally:
            async with self._connections_lock:
                self._connections.pop(device_id, None)

    async def _authenticate(
        self,
        websocket,
    ) -> Optional[str]:
        device_id = None
        
        try:
            # 尝试从 headers 中获取 device-id
            headers = dict(websocket.request.headers)
            device_id = headers.get("device-id") or headers.get("device_id")
            logger.info(f"Headers: {headers}")

            # 如果 headers 中没有，尝试从 URL 查询参数中获取
            if not device_id:
                from urllib.parse import parse_qs, urlparse
                request_path = websocket.request.path or ""
                logger.info(f"Request path: {request_path}")
                parsed_url = urlparse(request_path)
                query_params = parse_qs(parsed_url.query)
                logger.info(f"Query params: {query_params}")
                device_id = query_params.get("device-id", [None])[0]

            # 测试用：如果没有 device-id，使用默认值
            if not device_id:
                device_id = "test-device-default"
            logger.info(f"Device ID: {device_id}")

            # 检查设备是否在允许列表中
            if self.allowed_devices and device_id not in self.allowed_devices:
                logger.warning(f"Device {device_id} not in allowed list")
                return None

            # 检查认证 token
            if self.auth_enabled:
                token = headers.get("authorization", "")
                if token.startswith("Bearer "):
                    token = token[7:]
                if not self._verify_token(token, device_id):
                    logger.warning(f"Invalid token for device {device_id}")
                    return None

        except Exception as e:
            logger.error(f"Error in authentication: {e}")
            # 即使出错，也返回默认 device-id 以允许连接
            device_id = "test-device-default"
            logger.info(f"Using default device ID: {device_id}")

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
        parsed = self._protocol.parse(message)
        if parsed is None:
            logger.warning(f"Failed to parse message from {conn.device_id}")
            return

        if isinstance(parsed, HelloMessage):
            await self._handle_hello_obj(conn, parsed)
        elif isinstance(parsed, AudioMessage):
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
                logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_hello_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: HelloMessage,
    ) -> None:
        conn.client_id = msg.client_id
        conn.version = msg.version
        conn.audio_config = msg.audio_config or {}

        conn.voice_processor = None

        # 根据文档规范，返回符合格式的 hello 响应
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
        )
        await conn.websocket.send(response)
        logger.info(f"HELLO processed for {conn.device_id}")

    async def _handle_audio_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: AudioMessage,
    ) -> None:
        if conn.voice_processor is None:
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()

        if conn.state == DeviceState.SPEAKING:
            await self._handle_abort(conn)

        conn.state = DeviceState.LISTENING
        
        # 处理 Opus 编码的音频
        audio_data = msg.data
        if msg.format == AudioFormat.OPUS:
            try:
                # 初始化 Opus 解码器（如果需要）
                if conn._opus_decoder is None:
                    conn._opus_decoder = OpusDecoder(
                        sample_rate=msg.sample_rate,
                        channels=msg.channels,
                    )
                # 解码 Opus 到 PCM
                audio_data = conn._opus_decoder.decode(audio_data)
            except OpusCodecError as e:
                logger.error(f"Opus decode error: {e}")
                return
        
        conn._audio_buffer.append(audio_data)

        text = await conn.voice_processor.process_audio(audio_data, msg.sample_rate)
        if text:
            await self._process_user_text(conn, text)

    async def _handle_text_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: TextMessage,
    ) -> None:
        if msg.type == MessageType.START_LISTEN:
            conn.state = DeviceState.LISTENING
            if conn.voice_processor:
                conn.voice_processor.reset()
            return
        elif msg.type == MessageType.STOP_LISTEN:
            conn.state = DeviceState.IDLE
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
            conn.state = DeviceState.LISTENING
            if conn.voice_processor:
                conn.voice_processor.reset()
            logger.info(f"LISTEN start for {conn.device_id}")
        elif msg.state == ListenState.STOP:
            conn.state = DeviceState.IDLE
            logger.info(f"LISTEN stop for {conn.device_id}")
        elif msg.state == ListenState.DETECT:
            logger.info(f"Wake word detected for {conn.device_id}: {msg.text}")

    async def _handle_tts_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: TTSMessage,
    ) -> None:
        if msg.state == TTSState.START:
            logger.info(f"TTS start for {conn.device_id}")
        elif msg.state == TTSState.STOP:
            logger.info(f"TTS stop for {conn.device_id}")
        elif msg.state == TTSState.SENTENCE_START:
            logger.info(f"TTS sentence: {msg.text}")

    async def _handle_stt_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: STTMessage,
    ) -> None:
        logger.info(f"STT result for {conn.device_id}: {msg.text} (final={msg.is_final})")

    async def _handle_llm_obj(
        self,
        conn: ESP32DeviceConnection,
        msg: LLMMessage,
    ) -> None:
        logger.info(f"LLM emotion for {conn.device_id}: {msg.emotion}, text: {msg.text}")

    async def _handle_hello(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        conn.client_id = msg.get("client_id", msg.get("client-id", ""))
        conn.version = msg.get("version", "")
        conn.audio_config = msg.get("audio_params", msg.get("audio_config", {}))

        # Don't initialize voice processor here, do it lazily when needed
        conn.voice_processor = None

        # 根据文档规范，返回符合格式的 hello 响应
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
        )
        await conn.websocket.send(response)
        logger.info(f"HELLO processed for {conn.device_id}")

    async def _handle_audio(
        self,
        conn: ESP32DeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        if conn.voice_processor is None:
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()

        if conn.state == DeviceState.SPEAKING:
            await self._handle_abort(conn)

        conn.state = DeviceState.LISTENING
        
        # 获取音频数据
        audio_data = msg.get("data", b"")
        if isinstance(audio_data, str):
            import base64
            audio_data = base64.b64decode(audio_data)
        
        audio_config = msg.get("audio_config", {})
        sample_rate = audio_config.get("sample_rate", 16000)
        
        conn._audio_buffer.append(audio_data)

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
            conn.state = DeviceState.LISTENING
            if conn.voice_processor:
                conn.voice_processor.reset()
            return
        elif msg_type == MessageType.STOP_LISTEN.value:
            conn.state = DeviceState.IDLE
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
        conn.state = DeviceState.IDLE
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
                
                logger.info(f"Audio channel closed for {device_id}")
            except Exception:
                logger.exception(f"Error closing audio channel for {device_id}")

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
        conn.state = DeviceState.PROCESSING
        logger.info(f"Processing text from {conn.device_id}: {text}")

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
        logger.info(f"Built AgentRequest: session_id={request.session_id}, user_id={request.user_id}")

        try:
            response_text = ""
            event_count = 0
            async for event in self._process(request):
                event_count += 1
                obj = getattr(event, "object", None)
                status = getattr(event, "status", None)
                logger.debug(f"Received event: object={obj}, status={status}")

                if obj == "message" and status:
                    from agentscope_runtime.engine.schemas.agent_schemas import RunStatus
                    if status == RunStatus.Completed:
                        logger.info(f"Received completed message event")
                        parts = self._message_to_content_parts(event)
                        logger.info(f"Message parts: {len(parts)} parts")
                        for p in parts:
                            part_type = getattr(p, "type", None)
                            part_text = getattr(p, "text", "") or getattr(p, "refusal", "") or ""
                            logger.info(f"Part: type={part_type}, text={part_text}")
                            if part_type == ContentType.TEXT:
                                response_text += part_text
                            elif part_type == ContentType.REFUSAL:
                                response_text += part_text

            logger.info(f"Processed {event_count} events, response_text length: {len(response_text)}")
            if response_text:
                logger.info(f"Sending speech response: {response_text}")
                await self._send_speech(conn, response_text)
            else:
                logger.warning(f"No response text to send for {conn.device_id}")

        except Exception:
            logger.exception(f"Error processing text from {conn.device_id}")
            await self._send_error(conn, 500, "Processing error")
        finally:
            conn.state = DeviceState.IDLE

    async def _send_speech(
        self,
        conn: ESP32DeviceConnection,
        text: str,
    ) -> None:
        conn.state = DeviceState.SPEAKING
        logger.info(f"Sending speech to {conn.device_id}: {text}")

        try:
            # 检查连接状态
            if not await self._check_connection(conn):
                logger.warning(f"Connection closed, cannot send speech to {conn.device_id}")
                conn.state = DeviceState.IDLE
                return

            # Initialize voice processor if needed
            if conn.voice_processor is None:
                logger.info(f"Initializing voice processor for {conn.device_id}")
                conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
                await conn.voice_processor.initialize()

            # 发送 TTS start 消息
            logger.info(f"Sending TTS start message to {conn.device_id}")
            tts_start = self._protocol.encode_tts_start()
            if not await self._send_with_retry(conn, tts_start):
                conn.state = DeviceState.IDLE
                return
            
            # 发送句子开始消息
            logger.info(f"Sending TTS sentence message to {conn.device_id}: {text}")
            tts_sentence = self._protocol.encode_tts_sentence(text)
            if not await self._send_with_retry(conn, tts_sentence):
                conn.state = DeviceState.IDLE
                return

            logger.info(f"Synthesizing speech for {conn.device_id}")
            audio_data = await conn.voice_processor.synthesize_speech(text)
            logger.info(f"Speech synthesized: {len(audio_data) if audio_data else 0} bytes")
            
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
                        
                        # 如果采样率不匹配，进行重采样
                        if tts_sample_rate != target_sample_rate:
                            logger.info(f"Resampling audio from {tts_sample_rate}Hz to {target_sample_rate}Hz")
                            audio_data = resample_audio(audio_data, tts_sample_rate, target_sample_rate)
                            logger.info(f"Resampled audio: {len(audio_data)} bytes")
                        
                        if conn._opus_encoder is None:
                            logger.info(f"Initializing Opus encoder for {conn.device_id}")
                            conn._opus_encoder = OpusEncoder(
                                sample_rate=target_sample_rate,
                                channels=conn.audio_config.get("channels", 1),
                            )
                        # 编码 PCM 到 Opus
                        logger.info(f"Encoding PCM to Opus for {conn.device_id}")
                        opus_frames = conn._opus_encoder.encode_stream(audio_data)
                        logger.info(f"Encoded {len(opus_frames)} Opus frames")
                        
                        # 分块发送 Opus 帧
                        for i, frame in enumerate(opus_frames):
                            if conn._is_aborted:
                                logger.info(f"Aborted sending Opus frames to {conn.device_id}")
                                break
                            if not await self._check_connection(conn):
                                logger.warning(f"Connection closed while sending Opus frames")
                                break
                            logger.debug(f"Sending Opus frame {i+1}/{len(opus_frames)} to {conn.device_id}")
                            if not await self._send_with_retry(conn, frame, max_retries=2):
                                logger.warning(f"Failed to send Opus frame {i+1}")
                                break
                            # 控制发送速度，确保音频连续性
                            await asyncio.sleep(0.005)  # 5ms 延迟，确保音频流畅
                    except OpusCodecError as e:
                        logger.error(f"Opus encode error: {e}")
                        # 回退到 PCM
                        logger.info(f"Falling back to PCM for {conn.device_id}")
                        if await self._check_connection(conn):
                            await self._send_with_retry(conn, audio_data)
                else:
                    # 发送 PCM 音频
                    logger.info(f"Sending PCM audio to {conn.device_id}: {len(audio_data)} bytes")
                    if await self._check_connection(conn):
                        await self._send_with_retry(conn, audio_data)
            
            # 发送 TTS stop 消息
            if not conn._is_aborted and await self._check_connection(conn):
                logger.info(f"Sending TTS stop message to {conn.device_id}")
                tts_stop = self._protocol.encode_tts_stop()
                await self._send_with_retry(conn, tts_stop)
                
        except asyncio.CancelledError:
            logger.info(f"Speech sending cancelled for {conn.device_id}")
            pass
        except Exception:
            logger.exception(f"Error sending speech to {conn.device_id}")
            # Fallback to text on error
            try:
                if await self._check_connection(conn):
                    logger.info(f"Falling back to text message for {conn.device_id}")
                    text_msg = self._protocol.encode_text(text)
                    await self._send_with_retry(conn, text_msg)
            except Exception:
                logger.exception(f"Error sending fallback text message to {conn.device_id}")
                pass

    async def _check_connection(self, conn: ESP32DeviceConnection) -> bool:
        """检查 WebSocket 连接是否正常"""
        if not conn.websocket:
            logger.warning(f"No websocket connection for {conn.device_id}")
            return False
        try:
            # 检查连接状态
            if hasattr(conn.websocket, 'open'):
                return conn.websocket.open
            return True
        except Exception:
            logger.exception(f"Error checking connection for {conn.device_id}")
            return False

    async def _send_with_retry(self, conn: ESP32DeviceConnection, data: Union[str, bytes], max_retries: int = 3) -> bool:
        """发送消息并在失败时重试"""
        for attempt in range(max_retries):
            if not await self._check_connection(conn):
                logger.warning(f"Connection closed, cannot send message to {conn.device_id}")
                return False
            try:
                await conn.websocket.send(data)
                # 减少发送间隔，确保音频连续性
                await asyncio.sleep(0.01)  # 10ms 延迟，避免设备处理不过来
                return True
            except Exception as e:
                logger.warning(f"Attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.2)  # 减少重试间隔
                else:
                    logger.error(f"All attempts failed to send message to {conn.device_id}")
                    return False

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
            logger.warning(f"Device not connected: {device_id}")
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
