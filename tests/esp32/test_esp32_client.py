#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESP32 Channel Test Client.

This script simulates an ESP32 device connecting to CoPaw's ESP32 Channel.
Usage:
    python test_esp32_client.py --device-id test-device-001
"""
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


async def test_text_mode(uri: str, device_id: str):
    """Test text-based communication."""
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
        
        # Wait for server response
        response = await ws.recv()
        logger.info(f"Server response: {response}")
        
        # Send text message
        while True:
            text = input("\nEnter message (or 'quit' to exit): ")
            if text.lower() == "quit":
                break
            
            text_msg = json.dumps({
                "type": "text",
                "text": text,
            })
            logger.info(f"Sending: {text_msg}")
            await ws.send(text_msg)
            
            # Wait for response (may be audio or text)
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=60.0)
                if isinstance(response, bytes):
                    logger.info(f"Received audio: {len(response)} bytes")
                else:
                    logger.info(f"Received: {response}")
            except asyncio.TimeoutError:
                logger.warning("No response within 60 seconds")
        
        logger.info("Closing connection...")


async def test_ping(uri: str, device_id: str):
    """Test ping/pong."""
    headers = {"device-id": device_id}
    
    logger.info(f"Connecting to {uri} with device-id={device_id}")
    
    async with websockets.connect(uri, additional_headers=headers) as ws:
        logger.info("Connected! Sending PING...")
        
        ping_msg = json.dumps({"type": "ping", "timestamp": 12345})
        await ws.send(ping_msg)
        
        response = await ws.recv()
        logger.info(f"PONG response: {response}")


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
        choices=["text", "ping"],
        default="text",
        help="Test mode: text (interactive) or ping (just test connection)",
    )
    
    args = parser.parse_args()
    uri = f"ws://{args.host}:{args.port}"
    
    if args.mode == "ping":
        await test_ping(uri, args.device_id)
    else:
        await test_text_mode(uri, args.device_id)


if __name__ == "__main__":
    asyncio.run(main())
