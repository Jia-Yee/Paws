#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simple WebSocket Server for testing."""
import asyncio
import logging
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def handle_connection(websocket, path):
    """Handle WebSocket connection."""
    logger.info(f"Client connected from {websocket.remote_address}")
    
    try:
        # Send hello message
        hello_msg = {
            "type": "hello",
            "device_id": "test-server",
            "version": "1.0.0",
            "capabilities": ["text", "audio"]
        }
        import json
        await websocket.send(json.dumps(hello_msg))
        logger.info("Sent hello message")
        
        # Handle messages
        async for message in websocket:
            logger.info(f"Received message: {message}")
            # Echo back the message
            await websocket.send(message)
            logger.info("Echoed message back")
    except websockets.ConnectionClosed:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"Error: {e}")

async def main():
    """Start WebSocket server."""
    server = await websockets.serve(
        handle_connection,
        "0.0.0.0",
        8081
    )
    logger.info("WebSocket server started on ws://0.0.0.0:8081")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
