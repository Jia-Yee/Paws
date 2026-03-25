#!/usr/bin/env python3
"""Test with legacy websockets implementation."""
import asyncio
import websockets
from websockets.legacy.server import serve as legacy_serve
from websockets.legacy.client import connect as legacy_connect

async def handler(websocket, path):
    print(f"Handler called! Path: {path}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def main():
    print("Starting server with legacy implementation...")
    
    # 使用 legacy serve
    async with legacy_serve(handler, "localhost", 9998) as server:
        print(f"Server started on ws://localhost:9998")
        
        print("Connecting client...")
        try:
            # 使用 legacy connect
            async with legacy_connect("ws://localhost:9998") as ws:
                print("Connected!")
                await ws.send("hello")
                response = await ws.recv()
                print(f"Response: {response}")
        except Exception as e:
            print(f"Client error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
