# -*- coding: utf-8 -*-
"""MQTT Channel for ESP32 devices."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

import paho.mqtt.client as mqtt

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


class VADStateTracker:
    """VAD状态跟踪器 - 用于检测语音的开始和结束"""
    
    def __init__(self, threshold=0.5, threshold_low=0.2, window_size=5, min_speech_frames=3):
        """初始化VAD状态跟踪器"""
        self.threshold = threshold  # 语音检测阈值
        self.threshold_low = threshold_low  # 低阈值（用于避免抖动）
        self.window_size = window_size  # 滑动窗口大小
        self.min_speech_frames = min_speech_frames  # 认为有语音的最小帧数
        
        self.voice_buffer = []  # 音频缓冲区
        self.is_speaking = False  # 当前是否正在说话
        self.speech_start_time = 0  # 语音开始时间
        self.silence_start_time = 0  # 静音开始时间
        self.silence_duration_threshold = 1.0  # 静音持续时间阈值（秒）
        
    def reset(self):
        """重置VAD状态"""
        self.voice_buffer = []
        self.is_speaking = False
        self.speech_start_time = 0
        self.silence_start_time = 0
    
    def process_frame(self, probability, current_time=None):
        """处理一帧音频的VAD结果"""
        if current_time is None:
            current_time = time.time()
        
        # 添加到缓冲区并保持窗口大小
        self.voice_buffer.append(probability)
        if len(self.voice_buffer) > self.window_size:
            self.voice_buffer.pop(0)
        
        # 计算滑动窗口内的语音概率
        if self.voice_buffer:
            avg_prob = sum(self.voice_buffer) / len(self.voice_buffer)
        else:
            avg_prob = 0
        
        # 状态判断
        if not self.is_speaking:
            # 检测语音开始
            if avg_prob > self.threshold:
                self.is_speaking = True
                self.speech_start_time = current_time
                self.silence_start_time = 0
                return True, False  # 开始说话，不是结束
        else:
            # 检测语音结束
            if avg_prob < self.threshold_low:
                if self.silence_start_time == 0:
                    self.silence_start_time = current_time
                elif current_time - self.silence_start_time >= self.silence_duration_threshold:
                    self.is_speaking = False
                    return False, True  # 不是开始，结束说话
            else:
                # 重置静音开始时间
                self.silence_start_time = 0
        
        return False, False  # 无状态变化


class ESP32MQTTDeviceConnection:
    """ESP32 MQTT device connection."""
    
    def __init__(
        self,
        device_id: str,
        client: mqtt.Client,
        protocol: XiaozhiProtocol,
    ):
        self.device_id = device_id
        self.client = client
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
        # VAD 相关
        self._vad_tracker = VADStateTracker()
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.just_woken_up = False
        self.skip_next_asr = False
        self.can_process_audio = False
        self.is_processing_dialogue = False
    
    def update_activity(self) -> None:
        self.last_activity = time.time()
    
    def is_idle(self, timeout_seconds: float = 300.0) -> bool:
        return time.time() - self.last_activity > timeout_seconds
    
    def reset_audio_states(self):
        """重置音频相关状态"""
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.just_woken_up = False
        self.skip_next_asr = False
        if hasattr(self, "_vad_tracker"):
            self._vad_tracker.reset()


class ESP32MQTTChannel(BaseChannel):
    """ESP32 MQTT Channel: MQTT broker for ESP32 devices.

    Protocol flow:
    1. ESP32 connects via MQTT
    2. ESP32 subscribes to topics
    3. ESP32 publishes HELLO message
    4. Channel processes messages via MQTT
    5. Responses are published back to ESP32
    """
    
    channel = "esp32-mqtt"
    uses_manager_queue = False
    
    def __init__(
        self,
        process: ProcessHandler,
        enabled: bool = True,
        host: str = "0.0.0.0",
        port: int = 1883,
        username: str = "",
        password: str = "",
        keepalive: int = 60,
        topic_prefix: str = "xiaozhi",
        auth_enabled: bool = False,
        auth_key: str = "",
        allowed_devices: Optional[List[str]] = None,
        voice_config: Optional[Dict[str, Any]] = None,
        on_reply_sent: OnReplySent = None,
    ):
        super().__init__(process, on_reply_sent=on_reply_sent)
        self.enabled = enabled
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self.topic_prefix = topic_prefix
        self.auth_enabled = auth_enabled
        self.auth_key = auth_key
        self.allowed_devices: Set[str] = set(allowed_devices or [])
        self.voice_config = voice_config or {}
        
        self._client: Optional[mqtt.Client] = None
        self._server_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._protocol = XiaozhiProtocol()
        
        self._connections: Dict[str, ESP32MQTTDeviceConnection] = {}
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
    def from_config(
        cls,
        process: ProcessHandler,
        config: Any,
        on_reply_sent: OnReplySent = None,
        **kwargs,
    ) -> "ESP32MQTTChannel":
        return cls(
            process=process,
            enabled=getattr(config, "enabled", True),
            host=getattr(config, "host", "0.0.0.0"),
            port=getattr(config, "port", 1883),
            username=getattr(config, "username", ""),
            password=getattr(config, "password", ""),
            keepalive=getattr(config, "keepalive", 60),
            topic_prefix=getattr(config, "topic_prefix", "xiaozhi"),
            auth_enabled=getattr(config, "auth_enabled", False),
            auth_key=getattr(config, "auth_key", ""),
            allowed_devices=getattr(config, "allowed_devices", None),
            voice_config=getattr(config, "voice_config", None),
            on_reply_sent=on_reply_sent,
        )
    
    @classmethod
    def from_env(
        cls,
        process: ProcessHandler,
        on_reply_sent: OnReplySent = None,
    ) -> "ESP32MQTTChannel":
        allowed_devices_env = os.getenv("ESP32_MQTT_ALLOWED_DEVICES", "")
        allowed_devices = [
            s.strip()
            for s in allowed_devices_env.split(",")
            if s.strip()
        ] if allowed_devices_env else None
        
        voice_config = {
            "vad_type": os.getenv("ESP32_MQTT_VAD_TYPE", "silero"),
            "vad_threshold": float(os.getenv("ESP32_MQTT_VAD_THRESHOLD", "0.5")),
            "asr_type": os.getenv("ESP32_MQTT_ASR_TYPE", "funasr"),
            "asr_model": os.getenv("ESP32_MQTT_ASR_MODEL", "paraformer-zh"),  # 🔧 关键修复：使用纯中文 Paraformer 模型
            "tts_type": os.getenv("ESP32_MQTT_TTS_TYPE", "edge"),
            "tts_voice": os.getenv("ESP32_MQTT_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            "sample_rate": int(os.getenv("ESP32_MQTT_SAMPLE_RATE", "16000")),
            "min_speech_duration_ms": int(os.getenv("ESP32_MQTT_MIN_SPEECH_DURATION_MS", "500")),
            "min_silence_duration_ms": int(os.getenv("ESP32_MQTT_MIN_SILENCE_DURATION_MS", "300")),
        }
        
        return cls(
            process=process,
            enabled=os.getenv("ESP32_MQTT_ENABLED", "true").lower() == "true",
            host=os.getenv("ESP32_MQTT_HOST", "0.0.0.0"),
            port=int(os.getenv("ESP32_MQTT_PORT", "1883")),
            username=os.getenv("ESP32_MQTT_USERNAME", ""),
            password=os.getenv("ESP32_MQTT_PASSWORD", ""),
            keepalive=int(os.getenv("ESP32_MQTT_KEEPALIVE", "60")),
            topic_prefix=os.getenv("ESP32_MQTT_TOPIC_PREFIX", "xiaozhi"),
            auth_enabled=os.getenv("ESP32_MQTT_AUTH_ENABLED", "false").lower() == "true",
            auth_key=os.getenv("ESP32_MQTT_AUTH_KEY", ""),
            allowed_devices=allowed_devices,
            voice_config=voice_config,
            on_reply_sent=on_reply_sent,
        )
    
    async def start(self) -> None:
        if not self.enabled:
            logger.info("ESP32 MQTT Channel is disabled")
            return
        
        try:
            # Create MQTT client
            self._client = mqtt.Client(client_id="paws-esp32-mqtt")
            
            # Set callbacks
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect
            
            # Set authentication if needed
            if self.username:
                self._client.username_pw_set(self.username, self.password)
            
            # Start MQTT broker
            logger.info(f"Starting ESP32 MQTT Channel on {self.host}:{self.port}")
            
            # Start in a separate thread
            self._stop_event.clear()
            self._server_thread = threading.Thread(target=self._run_mqtt_broker, daemon=True)
            self._server_thread.start()
            
            logger.info("ESP32 MQTT Channel started")
        except Exception:
            logger.exception("Failed to start ESP32 MQTT Channel")
    
    async def stop(self) -> None:
        if not self.enabled:
            return
        
        try:
            self._stop_event.set()
            if self._client:
                self._client.disconnect()
                self._client.loop_stop()
            if self._server_thread and self._server_thread.is_alive():
                self._server_thread.join(timeout=5.0)
            logger.info("ESP32 MQTT Channel stopped")
        except Exception:
            logger.exception("Failed to stop ESP32 MQTT Channel")
    
    def _run_mqtt_broker(self) -> None:
        """Run MQTT broker in a separate thread."""
        try:
            self._client.connect(self.host, self.port, self.keepalive)
            self._client.loop_forever()
        except Exception:
            logger.exception("MQTT broker error")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        logger.info(f"MQTT connected with result code {rc}")
        # Subscribe to device topics
        topic = f"{self.topic_prefix}/esp32/#"
        client.subscribe(topic)
        logger.info(f"Subscribed to topic: {topic}")
    
    def _on_message(self, client, userdata, msg):
        """Callback when MQTT message is received."""
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8")
            
            # Extract device ID from topic
            # Example topic: xiaozhi/esp32/device-id/message
            parts = topic.split("/")
            if len(parts) >= 3:
                device_id = parts[2]
                logger.debug(f"Received MQTT message from {device_id}: {payload[:100]}")
                
                # Handle message in async context
                asyncio.run_coroutine_threadsafe(
                    self._handle_message(device_id, payload),
                    asyncio.get_event_loop()
                )
        except Exception:
            logger.exception("Failed to handle MQTT message")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        logger.info(f"MQTT disconnected with result code {rc}")
    
    async def _handle_message(
        self,
        device_id: str,
        payload: str,
    ) -> None:
        """Handle MQTT message."""
        # Get or create connection
        async with self._connections_lock:
            if device_id not in self._connections:
                conn = ESP32MQTTDeviceConnection(
                    device_id=device_id,
                    client=self._client,
                    protocol=self._protocol,
                )
                self._connections[device_id] = conn
                logger.info(f"New MQTT connection: {device_id}")
            else:
                conn = self._connections[device_id]
        
        conn.update_activity()
        
        # Parse message
        parsed = self._protocol.parse(payload)
        if parsed is None:
            logger.warning(f"Failed to parse message from {device_id}")
            return
        
        # Handle different message types
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
        conn: ESP32MQTTDeviceConnection,
        msg: HelloMessage,
    ) -> None:
        conn.client_id = msg.client_id
        conn.version = msg.version
        conn.audio_config = msg.audio_config
        
        conn.voice_processor = None
        
        response = self._protocol.encode_hello(
            device_id="copaw-server",
            client_id="copaw",
            version="1.0",
            capabilities=["audio", "text", "streaming"],
        )
        await self._publish_to_device(conn, response)
        logger.info(f"HELLO processed for {conn.device_id}")
    
    async def _handle_audio_obj(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: AudioMessage,
    ) -> None:
        # 检查是否可以处理音频（在收到listen start消息后才处理）
        if not conn.can_process_audio:
            # 不处理音频，直接返回
            return
        
        # 检查是否正在处理对话（暂停音频处理）
        if conn.is_processing_dialogue:
            # 正在处理对话，暂停音频处理
            return
            
        if conn.voice_processor is None:
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()
        
        if conn.state == DeviceState.SPEAKING:
            await self._handle_abort(conn)
        
        conn.state = DeviceState.LISTENING
        
        # 处理音频数据
        audio_data = msg.data
        sample_rate = msg.sample_rate
        
        # Handle Opus encoded audio
        if msg.format == AudioFormat.OPUS:
            try:
                if conn._opus_decoder is None:
                    conn._opus_decoder = OpusDecoder(
                        sample_rate=sample_rate,
                        channels=msg.channels,
                    )
                audio_data = conn._opus_decoder.decode(audio_data)
            except OpusCodecError as e:
                logger.error(f"Opus decode error: {e}")
                return
        
        # 累积音频数据
        conn.client_audio_buffer.extend(audio_data)
        
        # VAD 检测
        if conn.voice_processor and hasattr(conn.voice_processor, 'vad') and conn.voice_processor.vad:
            try:
                # 对于 Opus 解码后的 PCM 数据，直接进行 VAD 检测
                vad_result = conn.voice_processor.vad.is_speech(audio_data, sample_rate=sample_rate)
                if isinstance(vad_result, bool):
                    probability = 1.0 if vad_result else 0.0
                else:
                    # 对于返回 probability 的 VAD 实现
                    probability = vad_result if hasattr(vad_result, 'probability') else (1.0 if vad_result.is_speech else 0.0)
                
                # 处理 VAD 状态
                speech_start, speech_end = conn._vad_tracker.process_frame(probability)
                
                if speech_start:
                    conn.client_have_voice = True
                    conn.client_voice_stop = False
                elif speech_end:
                    conn.client_voice_stop = True
            except Exception as e:
                logger.warning(f"VAD detection failed: {e}")
        
        # 如果设备刚刚被唤醒，短暂忽略VAD检测
        if conn.just_woken_up:
            conn.client_have_voice = True
            conn.client_voice_stop = False
            # 设置一个短暂延迟后恢复VAD检测
            if not hasattr(conn, "vad_resume_task") or (hasattr(conn, "vad_resume_task") and conn.vad_resume_task.done()):
                conn.vad_resume_task = asyncio.create_task(self._resume_vad_detection(conn))
        
        # 当检测到语音停止时，处理音频
        if conn.client_voice_stop and conn.client_have_voice:
            if len(conn.client_audio_buffer) > 0:
                audio_data = bytes(conn.client_audio_buffer)
                conn.client_audio_buffer = bytearray()
                conn.client_have_voice = False
                conn.client_voice_stop = False
                
                # 触发 ASR 处理
                if not conn.skip_next_asr:
                    text = await conn.voice_processor.process_audio(audio_data, sample_rate)
                    if text and text.strip():
                        logger.info(f"ASR result: '{text}'")
                        
                        # 发送STT消息给设备
                        stt_msg = self._protocol.encode_stt(text, is_final=True)
                        await self._publish_to_device(conn, stt_msg)
                        logger.info(f"[STT SENT] Device: {conn.device_id}, Text: {text}")
                        
                        # 设置标志，暂停音频处理，直到对话完成
                        conn.is_processing_dialogue = True
                        
                        # 处理用户文本
                        await self._process_user_text(conn, text)
                    else:
                        logger.info("ASR returned empty text")
                else:
                    # 跳过 ASR（用于唤醒词）
                    conn.skip_next_asr = False
                    logger.info("Skipping ASR for wake word")
    
    async def _handle_text_obj(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: TextMessage,
    ) -> None:
        msg_type = msg.type
        if msg_type == MessageType.START_LISTEN:
            conn.state = DeviceState.LISTENING
            if conn.voice_processor:
                conn.voice_processor.reset()
            return
        elif msg_type == MessageType.STOP_LISTEN:
            conn.state = DeviceState.IDLE
            return
        elif msg_type == MessageType.ABORT:
            await self._handle_abort(conn)
            return
        
        if msg.text:
            await self._process_user_text(conn, msg.text)
    
    async def _handle_listen_obj(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: ListenMessage,
    ) -> None:
        if msg.state == ListenState.START:
            conn.state = DeviceState.LISTENING
            if conn.voice_processor:
                conn.voice_processor.reset()
            # 重置音频处理状态
            conn.reset_audio_states()
            # 开始处理音频
            conn.can_process_audio = True
            logger.info(f"LISTEN start for {conn.device_id}")
        elif msg.state == ListenState.STOP:
            conn.state = DeviceState.IDLE
            # 停止处理音频
            conn.can_process_audio = False
            logger.info(f"LISTEN stop for {conn.device_id}")
        elif msg.state == ListenState.DETECT:
            # 检测到唤醒词
            logger.info(f"Wake word detected for {conn.device_id}: {msg.text}")
            # 重置音频状态
            conn.reset_audio_states()
            # 标记刚刚被唤醒
            conn.just_woken_up = True
            # 跳过下一次ASR（避免识别唤醒词）
            conn.skip_next_asr = True
            # 暂时不处理音频，等待listen start消息
            conn.can_process_audio = False
    
    async def _handle_tts_obj(
        self,
        conn: ESP32MQTTDeviceConnection,
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
        conn: ESP32MQTTDeviceConnection,
        msg: STTMessage,
    ) -> None:
        logger.info(f"STT result for {conn.device_id}: {msg.text} (final={msg.is_final})")
    
    async def _handle_llm_obj(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: LLMMessage,
    ) -> None:
        logger.info(f"LLM emotion for {conn.device_id}: {msg.emotion}, text: {msg.text}")
    
    async def _handle_hello(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        conn.client_id = msg.get("client_id", msg.get("client-id", ""))
        conn.version = msg.get("version", "")
        conn.audio_config = msg.get("audio_config", msg.get("audio_params", {}))
        
        conn.voice_processor = None
        
        response = self._protocol.encode_hello(
            device_id="copaw-server",
            client_id="copaw",
            version="1.0",
            capabilities=["audio", "text", "streaming"],
        )
        await self._publish_to_device(conn, response)
        logger.info(f"HELLO processed for {conn.device_id}")
    
    async def _handle_audio(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        if conn.voice_processor is None:
            conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
            await conn.voice_processor.initialize()
        
        if conn.state == DeviceState.SPEAKING:
            await self._handle_abort(conn)
        
        conn.state = DeviceState.LISTENING
        
        # Get audio data
        audio_data = msg.get("data", b"")
        if isinstance(audio_data, str):
            import base64
            audio_data = base64.b64decode(audio_data)
        
        audio_config = msg.get("audio_config", msg.get("audio_params", {}))
        sample_rate = audio_config.get("sample_rate", 16000)
        
        # Handle Opus encoding
        if audio_config.get("format") == "opus":
            try:
                if conn._opus_decoder is None:
                    conn._opus_decoder = OpusDecoder(
                        sample_rate=sample_rate,
                        channels=1,
                    )
                audio_data = conn._opus_decoder.decode(audio_data)
            except OpusCodecError as e:
                logger.error(f"Opus decode error: {e}")
                return
        
        conn._audio_buffer.append(audio_data)
        
        text = await conn.voice_processor.process_audio(audio_data, sample_rate)
        if text:
            await self._process_user_text(conn, text)
    
    async def _handle_text(
        self,
        conn: ESP32MQTTDeviceConnection,
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
        conn: ESP32MQTTDeviceConnection,
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
    
    async def _handle_ping(
        self,
        conn: ESP32MQTTDeviceConnection,
        msg: Dict[str, Any],
    ) -> None:
        response = self._protocol.encode_pong(msg.get("timestamp", 0))
        await self._publish_to_device(conn, response)
    
    async def _resume_vad_detection(self, conn: ESP32MQTTDeviceConnection):
        """延迟后恢复VAD检测"""
        await asyncio.sleep(0.5)  # 短暂延迟
        conn.just_woken_up = False
        logger.info(f"Resumed VAD detection for {conn.device_id}")
    
    async def _process_user_text(
        self,
        conn: ESP32MQTTDeviceConnection,
        text: str,
    ) -> None:
        conn.state = DeviceState.PROCESSING
        
        try:
            payload = {
                "channel_id": self.channel,
                "sender_id": conn.device_id,
                "content_parts": [
                    TextContent(type=ContentType.TEXT, text=text),
                ],
                "meta": {
                    "device_id": conn.device_id,
                    "client_id": conn.client_id,
                    "audio_config": conn.audio_config,
                },
            }
            
            if self.process:
                await self.process(payload)
        except Exception:
            logger.exception(f"Error processing text from {conn.device_id}")
            await self._send_error(conn, 500, "Processing error")
            conn.is_processing_dialogue = False
        finally:
            conn.state = DeviceState.IDLE
    
    async def _send_speech(
        self,
        conn: ESP32MQTTDeviceConnection,
        text: str,
    ) -> None:
        conn.state = DeviceState.SPEAKING
        
        try:
            # Initialize voice processor if needed
            if conn.voice_processor is None:
                conn.voice_processor = VoiceProcessor(config=self._voice_processor_config)
                await conn.voice_processor.initialize()
            
            # Send TTS start message
            tts_start = self._protocol.encode_tts_start()
            await self._publish_to_device(conn, tts_start)
            
            # Send sentence start message
            tts_sentence = self._protocol.encode_tts_sentence(text)
            await self._publish_to_device(conn, tts_sentence)
            
            audio_data = await conn.voice_processor.synthesize_speech(text)
            if audio_data and not conn._is_aborted:
                # Check if device supports Opus
                audio_format = AudioFormat.PCM
                if conn.audio_config.get("format") == "opus":
                    audio_format = AudioFormat.OPUS
                    # Encode to Opus
                    try:
                        if conn._opus_encoder is None:
                            conn._opus_encoder = OpusEncoder(
                                sample_rate=conn.audio_config.get("sample_rate", 16000),
                                channels=conn.audio_config.get("channels", 1),
                            )
                        # Encode PCM to Opus
                        opus_frames = conn._opus_encoder.encode_stream(audio_data)
                        for frame in opus_frames:
                            if not conn._is_aborted:
                                await self._publish_to_device(conn, frame, is_binary=True)
                    except OpusCodecError as e:
                        logger.error(f"Opus encode error: {e}")
                        # Fallback to PCM
                        await self._publish_to_device(conn, audio_data, is_binary=True)
                else:
                    # Send PCM audio
                    await self._publish_to_device(conn, audio_data, is_binary=True)
            
            # Send TTS stop message
            if not conn._is_aborted:
                tts_stop = self._protocol.encode_tts_stop()
                await self._publish_to_device(conn, tts_stop)
                
            # TTS完成后，发送listen消息让ESP32进入listening状态
            if not conn._is_aborted:
                logger.info(f"Sending listen start message to {conn.device_id}")
                listen_start = self._protocol.encode_start_listen(mode="auto")
                await self._publish_to_device(conn, listen_start)
                # 更新连接状态
                conn.state = DeviceState.LISTENING
                # 重置音频处理状态，准备接收新的语音
                conn.can_process_audio = True
                conn.reset_audio_states()
                # 清除对话处理标志，恢复音频处理
                conn.is_processing_dialogue = False
                logger.info(f"Device {conn.device_id} is now listening")
                
        except asyncio.CancelledError:
            conn.state = DeviceState.IDLE
            conn.is_processing_dialogue = False
            pass
        except Exception:
            logger.exception(f"Error sending speech to {conn.device_id}")
            conn.state = DeviceState.IDLE
            conn.is_processing_dialogue = False
            # Fallback to text on error
            try:
                text_msg = self._protocol.encode_text(text)
                await self._publish_to_device(conn, text_msg)
            except Exception:
                pass
    
    async def _send_error(
        self,
        conn: ESP32MQTTDeviceConnection,
        code: int,
        message: str,
    ) -> None:
        error_msg = self._protocol.encode_error(code, message)
        await self._publish_to_device(conn, error_msg)
    
    async def _publish_to_device(
        self,
        conn: ESP32MQTTDeviceConnection,
        message: Union[str, bytes],
        is_binary: bool = False,
    ) -> None:
        """Publish message to device."""
        try:
            topic = f"{self.topic_prefix}/server/{conn.device_id}/message"
            if is_binary:
                conn.client.publish(topic, message, qos=1)
            else:
                conn.client.publish(topic, message, qos=1)
        except Exception:
            logger.exception(f"Failed to publish to {conn.device_id}")
    
    async def send(
        self,
        to_handle: str,
        content_parts: List[OutgoingContentPart],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send content to ESP32 device."""
        device_id = to_handle
        
        async with self._connections_lock:
            if device_id not in self._connections:
                logger.warning(f"Device not found: {device_id}")
                return
            conn = self._connections[device_id]
        
        for part in content_parts:
            if part.type == ContentType.TEXT and part.text:
                await self._send_speech(conn, part.text)
            elif part.type == ContentType.AUDIO and part.audio:
                # Send audio directly
                await self._publish_to_device(conn, part.audio, is_binary=True)
    
    async def list_connections(self) -> List[Dict[str, Any]]:
        """List all connected ESP32 devices."""
        devices = []
        async with self._connections_lock:
            for device_id, conn in self._connections.items():
                devices.append({
                    "device_id": device_id,
                    "client_id": conn.client_id,
                    "version": conn.version,
                    "state": conn.state.value,
                    "audio_config": conn.audio_config,
                    "last_activity": conn.last_activity,
                })
        return devices
