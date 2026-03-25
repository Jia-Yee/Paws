#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simple ESP32 Channel Test Client."""
import asyncio
import logging
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_connection():
    """Test WebSocket connection."""
    uri = "ws://localhost:8080?device-id=test-device-001"
    logger.info(f"Connecting to {uri}")
    
    try:
        async with websockets.connect(uri) as ws:
            logger.info("Connected!")
            
            # Send HELLO
            hello_msg = {
                "type": "hello",
                "device_id": "test-device-001",
                "version": "1.0.0",
                "capabilities": ["text", "audio"],
                "audio_config": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "channels": 1,
                },
            }
            import json
            await ws.send(json.dumps(hello_msg))
            logger.info("Sent HELLO message")
            
            # Wait for response
            response = await ws.recv()
            logger.info(f"Received response: {response}")
            
            logger.info("Connection test successful!")
    except Exception as e:
        logger.error(f"Connection error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_connection())
