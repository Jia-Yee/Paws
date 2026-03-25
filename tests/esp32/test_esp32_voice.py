#!/usr/bin/env python3
"""ESP32 Channel Voice Interaction Test Client."""
import argparse
import asyncio
import json
import logging
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


async def test_hello(uri: str, device_id: str):
    """Test HELLO handshake."""
    headers = {"device-id": device_id}

    logger.info(f"Connecting to {uri} with device-id={device_id}")

    async with websockets.connect(uri, additional_headers=headers) as ws:
        logger.info("Connected! Sending HELLO message...")

        # Send HELLO
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
        logger.info(f"Sent: {hello_msg}")

        # Wait for server response
        response = await ws.recv()
        logger.info(f"Server HELLO response: {response}")

        return response


async def test_text_mode(uri: str, device_id: str, text: str = "你好"):
    """Test text-based communication with HELLO handshake."""
    headers = {"device-id": device_id}

    logger.info(f"Connecting to {uri} with device-id={device_id}")

    async with websockets.connect(uri, additional_headers=headers) as ws:
        # Send HELLO first
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

        # Wait for server HELLO response
        response = await ws.recv()
        logger.info(f"Server HELLO response: {response}")

        # Send text message
        text_msg = json.dumps({
            "type": "text",
            "text": text,
        })
        logger.info(f"Sending text: {text_msg}")
        await ws.send(text_msg)

        # Wait for response (may be text or audio)
        logger.info("Waiting for response...")
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=60.0)
            if isinstance(response, bytes):
                logger.info(f"Received audio: {len(response)} bytes")
            else:
                logger.info(f"Received text: {response}")
        except asyncio.TimeoutError:
            logger.warning("No response within 60 seconds")


async def test_audio_mode(uri: str, device_id: str):
    """Test audio-based communication."""
    import base64

    headers = {"device-id": device_id}

    logger.info(f"Connecting to {uri} with device-id={device_id}")

    async with websockets.connect(uri, additional_headers=headers) as ws:
        # Send HELLO first
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

        # Wait for server HELLO response
        response = await ws.recv()
        logger.info(f"Server HELLO response: {response}")

        # Generate some dummy audio data (1 second of silence at 16kHz)
        import struct
        sample_rate = 16000
        duration_seconds = 1
        num_samples = sample_rate * duration_seconds
        # Generate silence (zeros)
        audio_data = struct.pack("<" + "h" * num_samples, *([0] * num_samples))
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        # Send audio message
        audio_msg = json.dumps({
            "type": "audio",
            "audio_config": {
                "format": "pcm",
                "sample_rate": 16000,
                "channels": 1,
            },
            "data": audio_b64,
            "timestamp": 1234567890,
        })
        logger.info(f"Sending audio: {len(audio_data)} bytes")
        await ws.send(audio_msg)

        # Wait for response
        logger.info("Waiting for response...")
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=60.0)
            if isinstance(response, bytes):
                logger.info(f"Received audio: {len(response)} bytes")
            else:
                logger.info(f"Received: {response}")
        except asyncio.TimeoutError:
            logger.warning("No response within 60 seconds")


async def main():
    parser = argparse.ArgumentParser(description="ESP32 Channel Test Client")
    parser.add_argument(
        "--host",
        default="localhost",
        help="ESP32 Channel host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="ESP32 Channel port (default: 8080)",
    )
    parser.add_argument(
        "--device-id",
        default="test-device-001",
        help="Device ID to use",
    )
    parser.add_argument(
        "--mode",
        choices=["hello", "text", "audio", "all"],
        default="hello",
        help="Test mode: hello (just handshake), text (text interaction), audio (audio interaction), all (run all tests)",
    )
    parser.add_argument(
        "--text",
        default="你好",
        help="Text to send in text mode",
    )

    args = parser.parse_args()
    uri = f"ws://{args.host}:{args.port}"

    if args.mode == "hello":
        await test_hello(uri, args.device_id)
    elif args.mode == "text":
        await test_text_mode(uri, args.device_id, args.text)
    elif args.mode == "audio":
        await test_audio_mode(uri, args.device_id)
    elif args.mode == "all":
        logger.info("=" * 50)
        logger.info("Test 1: HELLO handshake")
        logger.info("=" * 50)
        await test_hello(uri, args.device_id)

        logger.info("\n" + "=" * 50)
        logger.info("Test 2: Text interaction")
        logger.info("=" * 50)
        await test_text_mode(uri, args.device_id, "你好")

        logger.info("\n" + "=" * 50)
        logger.info("Test 3: Audio interaction")
        logger.info("=" * 50)
        await test_audio_mode(uri, args.device_id)


if __name__ == "__main__":
    asyncio.run(main())
