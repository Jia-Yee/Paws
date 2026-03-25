#!/usr/bin/env python3
"""Test websockets 16.0 - using serve_forever approach."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def main():
    print("Starting server...")
    
    # 使用 serve 作为上下文管理器
    async with serve(handler, "localhost", 9999) as server:
        print("Server started, connecting...")
        
        # 在同一个事件循环中连接
        async with websockets.connect("ws://localhost:9999") as ws:
            print("Connected!")
            await ws.send("hello")
            response = await ws.recv()
            print(f"Response: {response}")
    
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
