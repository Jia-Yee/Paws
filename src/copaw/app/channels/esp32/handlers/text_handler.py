# -*- coding: utf-8 -*-
"""ESP32 text message handler."""
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..connection import ESP32DeviceConnection

logger = logging.getLogger(__name__)


async def handle_text_message(conn: "ESP32DeviceConnection", message):
    """处理文本消息"""
    try:
        data = json.loads(message)
        
        msg_type = data.get("type")
        
        if msg_type == "hello":
            await handle_hello_message(conn, data)
        elif msg_type == "listen":
            await handle_listen_message(conn, data)
        elif msg_type == "text":
            await handle_text_content_message(conn, data)
        elif msg_type == "abort":
            await handle_abort_message(conn, data)
        else:
            logger.warning(f"未知消息类型: {msg_type}")
    except Exception as e:
        logger.error(f"处理文本消息失败: {e}")


async def handle_hello_message(conn: "ESP32DeviceConnection", data):
    """处理hello消息"""
    try:
        conn.client_id = data.get("client_id", conn.device_id)
        conn.version = data.get("version", "1.0.0")
        conn.audio_config = data.get("audio_params", {})
        
        # 生成会话ID
        conn.session_id = str(uuid.uuid4())
        
        # 发送响应 - 必须符合 xiaozhi-esp32 协议规范
        response = {
            "type": "hello",
            "transport": "websocket",
            "session_id": conn.session_id,
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60
            }
        }
        
        await conn.websocket.send(json.dumps(response))
        logger.info(f"设备 {conn.device_id} 握手成功，会话ID: {conn.session_id}")
        
        # 初始化活动时间戳
        conn.first_activity_time = time.time() * 1000
        conn.last_activity_time = time.time() * 1000
        
        # 初始化音频处理标志
        conn.can_process_audio = False
        
    except Exception as e:
        logger.error(f"处理hello消息失败: {e}")


async def handle_listen_message(conn: "ESP32DeviceConnection", data):
    """处理listen消息"""
    try:
        state = data.get("state")
        text = data.get("text", "")
        
        if state == "start":
            conn.state = "listening"
            # 重置音频状态，准备接收新的语音
            conn.reset_audio_states()
            # 重置VAD跟踪器
            if hasattr(conn, "_vad_tracker"):
                conn._vad_tracker.reset()
            # 清除音频缓冲区
            if hasattr(conn, "client_audio_buffer"):
                conn.client_audio_buffer = bytearray()
            # 清除just_woken_up标志，开始正常VAD检测
            conn.just_woken_up = False
            # 清除跳过ASR标志
            conn.skip_next_asr = False
            # 标记可以开始处理音频
            conn.can_process_audio = True
            logger.info(f"设备 {conn.device_id} 开始监听")
        elif state == "stop":
            conn.state = "idle"
            logger.info(f"设备 {conn.device_id} 停止监听")
        elif state == "detect":
            conn.just_woken_up = True
            # 标记需要跳过当前的ASR识别（避免识别唤醒词本身）
            conn.skip_next_asr = True
            # 重置can_process_audio，等待start消息后才处理音频
            conn.can_process_audio = False
            logger.info(f"设备 {conn.device_id} 检测到唤醒词: {text}")
    except Exception as e:
        logger.error(f"处理listen消息失败: {e}")


async def handle_text_content_message(conn: "ESP32DeviceConnection", data):
    """处理文本内容消息"""
    try:
        text = data.get("text", "")
        if text:
            logger.info(f"收到设备 {conn.device_id} 的文本: {text}")
            # 发送STT消息
            import json
            stt_msg = json.dumps({
                "type": "stt",
                "text": text,
                "session_id": conn.session_id,
                "is_final": True
            })
            await conn.websocket.send(stt_msg)
            logger.info(f"[STT SENT] Device: {conn.device_id}, Text: {text}")
            
            # 处理用户文本（需要通过正确的渠道处理）
            # 这里暂时记录日志，后续需要与 copaw 核心集成
            logger.info(f"需要处理用户文本: {text}")
    except Exception as e:
        logger.error(f"处理文本内容消息失败: {e}")


async def handle_abort_message(conn: "ESP32DeviceConnection", data):
    """处理abort消息"""
    try:
        if conn._speaking_task and not conn._speaking_task.done():
            conn._speaking_task.cancel()
        conn.client_is_speaking = False
        logger.info(f"设备 {conn.device_id} 中止播放")
    except Exception as e:
        logger.error(f"处理abort消息失败: {e}")
