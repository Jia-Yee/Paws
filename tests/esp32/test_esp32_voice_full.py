#!/usr/bin/env python3
"""ESP32 Channel Voice Interaction Test - Complete flow from audio to audio."""
import argparse
import asyncio
import base64
import json
import logging
import struct
import sys
import wave
import os
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def generate_test_audio(text: str = "你好", sample_rate: int = 16000) -> bytes:
    """Generate test audio using TTS or return silence if TTS not available."""
    # Try to use edge-tts to generate real audio
    try:
        import edge_tts
        
        async def generate():
            communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            return audio_data
        
        # Run in a new event loop
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            mp3_data = loop.run_until_complete(generate())
        finally:
            loop.close()
        
        # Convert MP3 to PCM
        if mp3_data:
            return mp3_to_pcm(mp3_data, sample_rate)
    except Exception as e:
        logger.warning(f"Could not generate TTS audio: {e}")
    
    # Fallback: generate silence
    logger.info("Generating silence audio for testing")
    duration_seconds = 2
    num_samples = sample_rate * duration_seconds
    return struct.pack("<" + "h" * num_samples, *([0] * num_samples))


def mp3_to_pcm(mp3_data: bytes, sample_rate: int = 16000) -> bytes:
    """Convert MP3 audio to PCM format."""
    try:
        import subprocess
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
            mp3_file.write(mp3_data)
            mp3_path = mp3_file.name
        
        pcm_path = mp3_path.replace(".mp3", ".pcm")
        
        # Use ffmpeg to convert
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", mp3_path,
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(sample_rate), "-ac", "1",
                pcm_path
            ],
            capture_output=True,
            text=True
        )
        
        os.unlink(mp3_path)
        
        if result.returncode == 0:
            with open(pcm_path, "rb") as f:
                pcm_data = f.read()
            os.unlink(pcm_path)
            return pcm_data
        else:
            logger.warning(f"ffmpeg error: {result.stderr}")
            
    except Exception as e:
        logger.warning(f"Could not convert MP3 to PCM: {e}")
    
    # Fallback to silence
    duration_seconds = 2
    num_samples = sample_rate * duration_seconds
    return struct.pack("<" + "h" * num_samples, *([0] * num_samples))


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Convert PCM data to WAV format for playback."""
    import io
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return wav_buffer.getvalue()


async def test_voice_interaction(
    uri: str,
    device_id: str,
    text: str = "你好",
    use_real_audio: bool = False,
    save_audio: bool = False,
):
    """Test complete voice interaction: audio -> ASR -> Agent -> TTS -> audio."""
    headers = {"device-id": device_id}
    
    logger.info(f"Connecting to {uri} with device-id={device_id}")
    
    async with websockets.connect(uri, additional_headers=headers) as ws:
        # Step 1: HELLO handshake
        logger.info("=" * 50)
        logger.info("Step 1: HELLO handshake")
        logger.info("=" * 50)
        
        hello_msg = json.dumps({
            "type": "hello",
            "device_id": device_id,
            "version": "1.0.0",
            "capabilities": ["text", "audio"],
            "audio_config": {
                "format": "pcm",
                "sample_rate": 16000,
                "channels": 1,
            },
        })
        await ws.send(hello_msg)
        logger.info(f"Sent HELLO")
        
        response = await asyncio.wait_for(ws.recv(), timeout=10.0)
        logger.info(f"Server HELLO response: {response[:100]}...")
        
        # Step 2: Send audio data
        logger.info("\n" + "=" * 50)
        logger.info("Step 2: Send audio data")
        logger.info("=" * 50)
        
        sample_rate = 16000
        
        if use_real_audio:
            logger.info(f"Generating real audio for text: '{text}'")
            audio_data = generate_test_audio(text, sample_rate)
        else:
            # Generate silence (VAD will detect no speech, but we'll send it anyway)
            logger.info("Generating silence audio for testing")
            duration_seconds = 3
            num_samples = sample_rate * duration_seconds
            audio_data = struct.pack("<" + "h" * num_samples, *([0] * num_samples))
        
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        
        audio_msg = json.dumps({
            "type": "audio",
            "audio_config": {
                "format": "pcm",
                "sample_rate": sample_rate,
                "channels": 1,
            },
            "data": audio_b64,
            "timestamp": 1234567890,
        })
        
        logger.info(f"Sending audio: {len(audio_data)} bytes")
        await ws.send(audio_msg)
        
        # Step 3: Wait for response (may take time for ASR + Agent + TTS)
        logger.info("\n" + "=" * 50)
        logger.info("Step 3: Wait for response")
        logger.info("=" * 50)
        logger.info("Processing: VAD -> ASR -> Agent -> TTS...")
        
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=120.0)
            
            if isinstance(response, bytes):
                logger.info(f"Received audio response: {len(response)} bytes")
                
                if save_audio:
                    # Save as WAV file
                    wav_data = pcm_to_wav(response, sample_rate)
                    output_path = Path("/Users/jia/workspace/HardClaw/Paws/test_output_response.wav")
                    with open(output_path, "wb") as f:
                        f.write(wav_data)
                    logger.info(f"Saved audio to: {output_path}")
            else:
                logger.info(f"Received text response: {response}")
                
        except asyncio.TimeoutError:
            logger.warning("No response within 120 seconds")
            logger.info("This might be expected if VAD detected no speech")
            logger.info("Try with --real-audio to generate actual speech audio")
        
        # Step 4: Also test text mode for comparison
        logger.info("\n" + "=" * 50)
        logger.info("Step 4: Test text mode (for comparison)")
        logger.info("=" * 50)
        
        text_msg = json.dumps({
            "type": "text",
            "text": text,
        })
        logger.info(f"Sending text: {text}")
        await ws.send(text_msg)
        
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=60.0)
            if isinstance(response, bytes):
                logger.info(f"Received audio response: {len(response)} bytes")
                
                if save_audio:
                    wav_data = pcm_to_wav(response, sample_rate)
                    output_path = Path("/Users/jia/workspace/HardClaw/Paws/test_output_text_response.wav")
                    with open(output_path, "wb") as f:
                        f.write(wav_data)
                    logger.info(f"Saved audio to: {output_path}")
            else:
                logger.info(f"Received: {response}")
        except asyncio.TimeoutError:
            logger.warning("No response within 60 seconds")


async def test_audio_only(uri: str, device_id: str):
    """Test sending raw binary audio data."""
    headers = {"device-id": device_id}
    
    logger.info(f"Testing binary audio mode")
    
    async with websockets.connect(uri, additional_headers=headers) as ws:
        # HELLO
        hello_msg = json.dumps({
            "type": "hello",
            "device_id": device_id,
            "version": "1.0.0",
            "audio_config": {"format": "pcm", "sample_rate": 16000, "channels": 1},
        })
        await ws.send(hello_msg)
        await ws.recv()
        logger.info("HELLO done")
        
        # Send binary audio directly
        sample_rate = 16000
        duration_seconds = 2
        num_samples = sample_rate * duration_seconds
        audio_data = struct.pack("<" + "h" * num_samples, *([0] * num_samples))
        
        logger.info(f"Sending binary audio: {len(audio_data)} bytes")
        await ws.send(audio_data)
        
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=30.0)
            if isinstance(response, bytes):
                logger.info(f"Received audio: {len(response)} bytes")
            else:
                logger.info(f"Received: {response}")
        except asyncio.TimeoutError:
            logger.info("No response (expected for silence)")


async def main():
    parser = argparse.ArgumentParser(description="ESP32 Voice Interaction Test")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=8080, help="Server port")
    parser.add_argument("--device-id", default="test-voice-001", help="Device ID")
    parser.add_argument("--text", default="你好", help="Text to synthesize for testing")
    parser.add_argument("--real-audio", action="store_true", help="Generate real audio using TTS")
    parser.add_argument("--save-audio", action="store_true", help="Save response audio to file")
    parser.add_argument("--mode", choices=["voice", "binary", "all"], default="voice", help="Test mode")
    
    args = parser.parse_args()
    uri = f"ws://{args.host}:{args.port}"
    
    if args.mode == "voice":
        await test_voice_interaction(
            uri, args.device_id, args.text, args.real_audio, args.save_audio
        )
    elif args.mode == "binary":
        await test_audio_only(uri, args.device_id)
    elif args.mode == "all":
        logger.info("=" * 60)
        logger.info("Test 1: Voice interaction")
        logger.info("=" * 60)
        await test_voice_interaction(
            uri, args.device_id, args.text, args.real_audio, args.save_audio
        )
        
        logger.info("\n" + "=" * 60)
        logger.info("Test 2: Binary audio")
        logger.info("=" * 60)
        await test_audio_only(uri, args.device_id)


if __name__ == "__main__":
    asyncio.run(main())
