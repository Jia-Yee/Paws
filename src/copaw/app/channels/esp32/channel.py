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
import queue
import threading
import time
import wave  # Added for debug audio saving
from concurrent.futures import ThreadPoolExecutor
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
from .connection import ESP32DeviceConnection
from .handlers.text_handler import handle_text_message
from .audio.audio_processor import handle_audio_message
from .utils import calculate_audio_duration_ms, pcm_to_wav, resample_audio
from ....voice import VoiceProcessor, VoiceProcessorConfig
from ....voice.opus import OpusDecoder, OpusEncoder, OpusCodecError

if TYPE_CHECKING:
    from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest

logger = logging.getLogger(__name__)





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
        self._idle_check_task: Optional[asyncio.Task] = None

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
        # 🔧 关键修复：添加 WebSocket 心跳参数，避免连接超时
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            # 设置心跳间隔为 20 秒，超时为 10 秒
            ping_interval=20.0,
            ping_timeout=10.0
        )
        logger.info(f"ESP32 channel started on ws://{self.host}:{self.port}")
        
        # 启动空闲检查任务
        self._idle_check_task = asyncio.create_task(self._check_idle_connections())
        logger.info("Idle connection check task started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._idle_check_task:
            self._idle_check_task.cancel()
            try:
                await self._idle_check_task
            except asyncio.CancelledError:
                pass
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
            channel=self,
        )

        # 🔧 新增：设置事件循环
        conn.loop = asyncio.get_running_loop()
        
        # 初始化音频组件
        try:
            # 初始化VAD
            from copaw.voice.vad import create_vad
            conn.vad = create_vad(vad_type="silero", sample_rate=16000, threshold=0.5)
            logger.info(f"VAD initialized for {conn.device_id}")
            
            # 初始化ASR
            from copaw.voice.processor import VoiceProcessor
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()
            conn.asr = conn.voice_processor
            logger.info(f"ASR initialized for {conn.device_id}")
            
            # 初始化TTS
            from copaw.voice.tts import EdgeTTS
            conn.tts = EdgeTTS()
            logger.info(f"TTS initialized for {conn.device_id}")
        except Exception as e:
            logger.error(f"Error initializing audio components: {e}")

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
            # 🔧 新增：停止事件
            conn.stop_event.set()
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
            
            logger.info(f"Request path: {request_path}")
            
            from urllib.parse import parse_qs, urlparse
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
        # 记录接收到的消息类型和大小
        if isinstance(message, bytes):
            logger.info(f"Received binary data from {conn.device_id}: {len(message)} bytes")
            # 处理音频消息
            await handle_audio_message(conn, message)
        else:
            logger.debug(f"Received text message from {conn.device_id}: {message[:200]}{'...' if len(str(message)) > 200 else ''}")
            # 处理文本消息
            await handle_text_message(conn, message)


        







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
            conn.state = DeviceState.IDLE
            conn.is_processing_dialogue = False

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
            tts_start = self._protocol.encode_tts_start(session_id=conn.session_id)
            if not await self._send_with_retry(conn, tts_start):
                conn.state = DeviceState.IDLE
                return
            
            # 发送句子开始消息
            logger.info(f"Sending TTS sentence message to {conn.device_id}: {text}")
            tts_sentence = self._protocol.encode_tts_sentence(text, session_id=conn.session_id)
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
                tts_stop = self._protocol.encode_tts_stop(session_id=conn.session_id)
                await self._send_with_retry(conn, tts_stop)
            
            # TTS完成后，发送listen消息让ESP32进入listening状态
            if await self._check_connection(conn):
                logger.info(f"Sending listen start message to {conn.device_id}")
                listen_start = self._protocol.encode_start_listen(mode="auto")
                await self._send_with_retry(conn, listen_start)
                # 更新连接状态
                conn.state = DeviceState.LISTENING
                # 重置音频处理状态，准备接收新的语音
                conn.can_process_audio = True
                conn.reset_audio_states()
                if hasattr(conn, "_vad_tracker"):
                    conn._vad_tracker.reset()
                if hasattr(conn, "client_audio_buffer"):
                    conn.client_audio_buffer = bytearray()
                # 清除对话处理标志，恢复音频处理
                conn.is_processing_dialogue = False
                logger.info(f"Device {conn.device_id} is now listening")
                
        except asyncio.CancelledError:
            logger.info(f"Speech sending cancelled for {conn.device_id}")
            conn.state = DeviceState.IDLE
            conn.is_processing_dialogue = False
            pass
        except Exception:
            logger.exception(f"Error sending speech to {conn.device_id}")
            conn.state = DeviceState.IDLE
            conn.is_processing_dialogue = False
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

    async def _check_idle_connections(self) -> None:
        """定期检查空闲连接"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                async with self._connections_lock:
                    idle_devices = []
                    for device_id, conn in self._connections.items():
                        if conn.is_idle(timeout_seconds=300):  # 5分钟无活动
                            idle_devices.append(device_id)
                    
                    for device_id in idle_devices:
                        conn = self._connections.get(device_id)
                        if conn:
                            logger.info(f"Device {device_id} has been idle for too long, sending wait state")
                            try:
                                # 发送状态消息通知设备进入等待状态
                                state_msg = self._protocol.encode_state(
                                    state=DeviceState.IDLE,
                                    message="Idle timeout, waiting for wake word"
                                )
                                await conn.websocket.send(state_msg)
                                logger.info(f"Sent wait state to {device_id}")
                            except Exception as e:
                                logger.error(f"Error sending wait state to {device_id}: {e}")
                                # 如果发送失败，关闭连接
                                try:
                                    await conn.websocket.close()
                                except Exception:
                                    pass
                                self._connections.pop(device_id, None)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error checking idle connections: {e}")

    async def _send_with_retry(self, conn: ESP32DeviceConnection, data: str | bytes, max_retries: int = 3) -> bool:
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
