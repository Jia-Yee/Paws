#!/usr/bin/env python3
"""Test ESP32 Channel with Xiaozhi protocol (Opus audio)."""
import argparse
import asyncio
import json
import logging
import struct
import sys

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


async def test_xiaozhi_protocol(
    uri: str,
    device_id: str,
    use_opus: bool = True,
):
    """Test Xiaozhi protocol with ESP32 Channel."""
    
    # 模拟 xiaozhi-esp32 的 HELLO 消息
    hello_msg = {
        "type": "hello",
        "version": 1,
        "features": {"aec": True, "mcp": True},
        "transport": "websocket",
        "audio_params": {
            "format": "opus" if use_opus else "pcm",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    
    headers = {
        "device-id": device_id,
        "client-id": f"client-{device_id}",
        "protocol-version": "1",
    }
    
    logger.info(f"Connecting to {uri} with device-id={device_id}")
    
    async with websockets.connect(uri, additional_headers=headers) as ws:
        # Step 1: Send HELLO
        logger.info("=" * 50)
        logger.info("Step 1: Send HELLO (Xiaozhi format)")
        logger.info("=" * 50)
        await ws.send(json.dumps(hello_msg))
        logger.info(f"Sent: {json.dumps(hello_msg, indent=2)}")
        
        # Wait for server HELLO response
        response = await asyncio.wait_for(ws.recv(), timeout=10.0)
        logger.info(f"Server HELLO response: {response[:200]}...")
        
        # Step 2: Send LISTEN start
        logger.info("\n" + "=" * 50)
        logger.info("Step 2: Send LISTEN start")
        logger.info("=" * 50)
        listen_msg = {"type": "listen", "state": "start", "mode": "auto"}
        await ws.send(json.dumps(listen_msg))
        logger.info(f"Sent: {json.dumps(listen_msg)}")
        
        # Step 3: Send text message
        logger.info("\n" + "=" * 50)
        logger.info("Step 3: Send text message")
        logger.info("=" * 50)
        text_msg = {"type": "text", "text": "你好"}
        await ws.send(json.dumps(text_msg))
        logger.info(f"Sent: {json.dumps(text_msg)}")
        
        # Step 4: Receive response
        logger.info("\n" + "=" * 50)
        logger.info("Step 4: Receive response")
        logger.info("=" * 50)
        
        messages_received = []
        try:
            while True:
                response = await asyncio.wait_for(ws.recv(), timeout=60.0)
                if isinstance(response, bytes):
                    logger.info(f"Received audio: {len(response)} bytes")
                    messages_received.append(("audio", len(response)))
                else:
                    msg = json.loads(response)
                    msg_type = msg.get("type", "unknown")
                    logger.info(f"Received JSON: {msg_type}")
                    messages_received.append(("json", msg_type))
                    
                    # Check for TTS stop to end test
                    if msg_type == "tts" and msg.get("state") == "stop":
                        logger.info("TTS stop received, ending test")
                        break
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for response")
        
        # Step 5: Send LISTEN stop
        logger.info("\n" + "=" * 50)
        logger.info("Step 5: Send LISTEN stop")
        logger.info("=" * 50)
        listen_stop = {"type": "listen", "state": "stop"}
        await ws.send(json.dumps(listen_stop))
        logger.info(f"Sent: {json.dumps(listen_stop)}")
        
        # Summary
        logger.info("\n" + "=" * 50)
        logger.info("Test Summary")
        logger.info("=" * 50)
        logger.info(f"Messages received: {len(messages_received)}")
        for msg_type, content in messages_received:
            logger.info(f"  - {msg_type}: {content}")
        
        return len(messages_received) > 0


async def test_opus_audio(uri: str, device_id: str):
    """Test Opus audio encoding/decoding."""
    try:
        from copaw.voice.opus import OpusEncoder, OpusDecoder
    except ImportError:
        logger.error("Opus module not found")
        return False
    
    # Generate test PCM audio
    sample_rate = 16000
    duration_seconds = 1
    num_samples = sample_rate * duration_seconds
    pcm_data = struct.pack("<" + "h" * num_samples, *([0] * num_samples))
    
    # Encode to Opus
    encoder = OpusEncoder(sample_rate=sample_rate, channels=1)
    opus_frames = encoder.encode_stream(pcm_data)
    logger.info(f"Encoded {len(pcm_data)} bytes PCM to {len(opus_frames)} Opus frames")
    
    # Decode back to PCM
    decoder = OpusDecoder(sample_rate=sample_rate, channels=1)
    decoded_pcm = decoder.decode_stream(opus_frames)
    logger.info(f"Decoded {len(opus_frames)} Opus frames to {len(decoded_pcm)} bytes PCM")
    
    return True


async def main():
    parser = argparse.ArgumentParser(description="Test ESP32 Channel with Xiaozhi Protocol")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=8080, help="Server port")
    parser.add_argument("--device-id", default="xiaozhi-test-001", help="Device ID")
    parser.add_argument("--opus", action="store_true", default=True, help="Use Opus encoding")
    parser.add_argument("--test-opus", action="store_true", help="Test Opus encoding only")
    
    args = parser.parse_args()
    uri = f"ws://{args.host}:{args.port}"
    
    if args.test_opus:
        await test_opus_audio(uri, args.device_id)
    else:
        success = await test_xiaozhi_protocol(uri, args.device_id, args.opus)
        if success:
            logger.info("\n✅ Test passed!")
        else:
            logger.error("\n❌ Test failed!")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
