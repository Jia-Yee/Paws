# -*- coding: utf-8 -*-
"""ESP32 audio processor."""
import logging
import time
import asyncio
import opuslib_next
import numpy as np
from collections import deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..connection import ESP32DeviceConnection

logger = logging.getLogger(__name__)


class VADStateTracker:
    """VAD状态跟踪器 - 跟踪语音活动的开始和结束"""
    
    def __init__(self, 
                 threshold: float = 0.5,
                 threshold_low: float = 0.2,
                 min_silence_duration_ms: int = 1000,
                 frame_window_threshold: int = 3):
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.silence_threshold_ms = min_silence_duration_ms
        self.frame_window_threshold = frame_window_threshold
        
        # 滑动窗口，用于判断是否有语音
        self.voice_window = deque(maxlen=5)
        self.last_is_voice = False
        
        # Opus解码器
        self._opus_decoder = None
        
    def init_decoder(self):
        """初始化Opus解码器"""
        if self._opus_decoder is None:
            self._opus_decoder = opuslib_next.Decoder(16000, 1)
        return self._opus_decoder
    
    def reset(self):
        """重置状态"""
        self.voice_window.clear()
        self.last_is_voice = False


async def handle_audio_message(conn: "ESP32DeviceConnection", audio: bytes):
    """处理音频消息 - 参考 xiaozhi-esp32-server 实现"""
    
    # 检查是否可以处理音频（在收到listen start消息后才处理）
    if not hasattr(conn, "can_process_audio") or not conn.can_process_audio:
        # 不处理音频，直接返回
        return
    
    # 检查是否正在处理对话（暂停音频处理）
    if hasattr(conn, "is_processing_dialogue") and conn.is_processing_dialogue:
        # 正在处理对话，暂停音频处理
        return
    
    # 初始化VAD状态跟踪器
    if not hasattr(conn, "_vad_tracker"):
        conn._vad_tracker = VADStateTracker(
            threshold=0.5,
            threshold_low=0.2,
            min_silence_duration_ms=1000,
            frame_window_threshold=3
        )
    
    vad_tracker = conn._vad_tracker
    
    # 当前片段是否有人说话
    have_voice = False
    if conn.vad:
        try:
            have_voice = await detect_voice_activity(conn, audio, vad_tracker)
        except Exception as e:
            logger.warning(f"VAD detection failed: {e}")
            have_voice = False
    
    # 如果设备刚刚被唤醒，短暂忽略VAD检测
    # 但继续接收音频，只是认为所有音频都有语音（避免丢失唤醒词后的语音）
    if hasattr(conn, "just_woken_up") and conn.just_woken_up:
        have_voice = True  # 强制认为有语音，继续接收音频
        # 设置一个短暂延迟后恢复VAD检测
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
    
    # manual 模式下不打断正在播放的内容
    if have_voice:
        if conn.client_is_speaking and conn.client_listen_mode != "manual":
            await handle_abort_message(conn)
    
    # 设备长时间空闲检测，用于say goodbye
    await no_voice_close_connect(conn, have_voice)
    
    # 接收音频 - 将音频数据放入队列，由 ASR 线程处理
    await receive_audio(conn, audio, have_voice)


async def detect_voice_activity(conn: "ESP32DeviceConnection", opus_packet: bytes, tracker: VADStateTracker) -> bool:
    """检测语音活动 - 使用滑动窗口和双阈值判断"""
    # manual 模式：直接返回True，不进行实时VAD检测，所有音频都缓存
    if conn.client_listen_mode == "manual":
        return True
    
    try:
        decoder = tracker.init_decoder()
        
        # 解码Opus到PCM
        pcm_frame = decoder.decode(opus_packet, 960)  # 60ms at 16kHz = 960 samples
        
        if not hasattr(conn, "client_audio_buffer"):
            conn.client_audio_buffer = bytearray()
        conn.client_audio_buffer.extend(pcm_frame)
        
        client_have_voice = False
        
        # 处理512样本的块 (Silero VAD需要)
        while len(conn.client_audio_buffer) >= 512 * 2:  # 512 samples * 2 bytes (int16)
            chunk = conn.client_audio_buffer[:512 * 2]
            conn.client_audio_buffer = conn.client_audio_buffer[512 * 2:]
            
            # 使用VAD检测 - 使用现有的VAD接口
            vad_result = conn.vad.is_speech(chunk, sample_rate=16000)
            speech_prob = vad_result.probability if hasattr(vad_result, 'probability') else (1.0 if vad_result.is_speech else 0.0)
            is_voice = vad_result.is_speech
            
            # 双阈值判断（如果probability可用）
            if hasattr(vad_result, 'probability'):
                if speech_prob >= tracker.threshold:
                    is_voice = True
                elif speech_prob <= tracker.threshold_low:
                    is_voice = False
                else:
                    is_voice = tracker.last_is_voice
            
            tracker.last_is_voice = is_voice
            
            # 更新滑动窗口
            tracker.voice_window.append(is_voice)
            client_have_voice = tracker.voice_window.count(True) >= tracker.frame_window_threshold
        
        return client_have_voice
        
    except opuslib_next.OpusError as e:
        logger.warning(f"Opus decode error: {e}")
        return False
    except Exception as e:
        logger.error(f"VAD processing error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def receive_audio(conn: "ESP32DeviceConnection", audio: bytes, audio_have_voice: bool):
    """接收音频数据 - 参考 xiaozhi-esp32-server ASR base"""
    
    # 累积音频数据
    conn.asr_audio.append(audio)
    
    if conn.client_listen_mode == "manual":
        # 手动模式：只缓存音频，不自动触发识别
        return
    
    # 自动/实时模式：使用VAD检测
    # 如果没有语音，且之前也没有声音，只保留最近10个包
    if not audio_have_voice and not conn.client_have_voice:
        conn.asr_audio = conn.asr_audio[-10:]
        return
    
    # 更新语音状态
    if audio_have_voice:
        conn.client_have_voice = True
        conn.last_activity_time = time.time() * 1000
        conn.client_voice_stop = False
    
    # 检测语音停止：之前有声音，现在没声音，且静音时间超过阈值
    if conn.client_have_voice and not audio_have_voice:
        silence_duration = time.time() * 1000 - conn.last_activity_time
        if silence_duration >= 1000:  # 1000ms 静音阈值
            conn.client_voice_stop = True
    
    # 当检测到语音停止时触发ASR
    if conn.client_voice_stop:
        asr_audio_task = conn.asr_audio.copy()
        conn.reset_audio_states()
        
        # 至少累积了15个包才处理（约1秒音频）
        if len(asr_audio_task) > 15:
            asyncio.create_task(handle_voice_stop(conn, asr_audio_task))


async def handle_voice_stop(conn: "ESP32DeviceConnection", asr_audio_task: list):
    """处理语音停止 - 执行ASR识别"""
    try:
        # 检查是否需要跳过ASR（唤醒词识别）
        if hasattr(conn, "skip_next_asr") and conn.skip_next_asr:
            logger.info("Skipping ASR for wake word")
            conn.skip_next_asr = False
            return
        
        logger.info(f"Voice stop detected, processing {len(asr_audio_task)} audio packets")
        
        # 解码Opus到PCM
        pcm_data = decode_opus_packets(asr_audio_task)
        
        if not pcm_data:
            logger.warning("No PCM data after decoding")
            return
        
        combined_pcm_data = b"".join(pcm_data)
        
        if len(combined_pcm_data) == 0:
            logger.warning("Empty audio data")
            return
        
        # 使用VoiceProcessor进行ASR识别
        if conn.voice_processor:
            text = await conn.voice_processor.process_audio(combined_pcm_data, 16000)
            
            if text and text.strip():
                logger.info(f"ASR result: '{text}'")
                
                # 发送STT消息给设备
                import json
                stt_msg = json.dumps({
                    "type": "stt",
                    "text": text,
                    "session_id": conn.session_id,
                    "is_final": True
                })
                await conn.websocket.send(stt_msg)
                logger.info(f"[STT SENT] Device: {conn.device_id}, Text: {text}")
                
                # 设置标志，暂停音频处理，直到对话完成
                conn.is_processing_dialogue = True
                
                # 触发后续处理（AI回复）
                # 通过channel调用AI处理
                from ..channel import ESP32Channel
                # 获取channel实例 - 通过connection的引用
                if hasattr(conn, '_channel'):
                    await start_to_chat(conn, conn._channel, text)
                else:
                    logger.warning("Channel reference not found in connection")
            else:
                logger.info("ASR returned empty text")
        else:
            logger.warning("No voice_processor available for ASR")
            
    except Exception as e:
        logger.error(f"Error handling voice stop: {e}")
        import traceback
        logger.error(traceback.format_exc())


def decode_opus_packets(opus_packets: list) -> list:
    """将Opus音频数据包解码为PCM数据"""
    decoder = None
    try:
        decoder = opuslib_next.Decoder(16000, 1)
        pcm_data = []
        buffer_size = 960  # 每次处理960个采样点 (60ms at 16kHz)
        
        for i, opus_packet in enumerate(opus_packets):
            try:
                if not opus_packet or len(opus_packet) == 0:
                    continue
                
                pcm_frame = decoder.decode(opus_packet, buffer_size)
                if pcm_frame and len(pcm_frame) > 0:
                    pcm_data.append(pcm_frame)
                    
            except opuslib_next.OpusError as e:
                logger.warning(f"Opus decode error at packet {i}: {e}")
            except Exception as e:
                logger.error(f"Audio processing error at packet {i}: {e}")
        
        return pcm_data
        
    except Exception as e:
        logger.error(f"Opus decoding failed: {e}")
        return []
    finally:
        if decoder is not None:
            try:
                del decoder
            except Exception:
                pass


async def start_to_chat(conn: "ESP32DeviceConnection", channel, text: str):
    """开始对话处理 - 发送给AI处理"""
    try:
        logger.info(f"Starting chat with text: {text}")
        
        # 调用channel的_process_user_text方法来处理AI对话
        if channel and hasattr(channel, '_process_user_text'):
            await channel._process_user_text(conn, text)
        else:
            logger.warning("Channel not available for processing text")
            
    except Exception as e:
        logger.error(f"Error in start_to_chat: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def resume_vad_detection(conn: "ESP32DeviceConnection"):
    """恢复VAD检测"""
    await asyncio.sleep(2)
    conn.just_woken_up = False
    logger.info(f"VAD detection resumed for {conn.device_id}")


async def no_voice_close_connect(conn: "ESP32DeviceConnection", have_voice: bool):
    """设备长时间空闲检测"""
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    
    # 只有在已经初始化过时间戳的情况下才进行超时检查
    if conn.last_activity_time > 0.0:
        no_voice_time = time.time() * 1000 - conn.last_activity_time
        close_connection_no_voice_time = 120  # 默认120秒
        
        if no_voice_time > 1000 * close_connection_no_voice_time:
            # 发送空闲状态通知
            await conn.protocol.send_state(conn, "idle")
            logger.info(f"Device {conn.device_id} idle for too long, entering idle state")


async def handle_abort_message(conn: "ESP32DeviceConnection"):
    """处理中止消息"""
    if conn._speaking_task and not conn._speaking_task.done():
        conn._speaking_task.cancel()
    conn.client_is_speaking = False
    logger.info(f"Aborted current playback for {conn.device_id}")
