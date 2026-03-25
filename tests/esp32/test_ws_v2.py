#!/usr/bin/env python3
"""Test websockets 16.0 with proper event loop handling."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def main():
    # 使用 async with 上下文管理器
    async with serve(handler, "localhost", 8888) as server:
        print("Server started on ws://localhost:8888")
        
        # 在同一个事件循环中运行客户端
        await asyncio.sleep(1)  # 等待服务器启动
        
        # 使用 connect
        try:
            async with websockets.connect("ws://localhost:8888") as ws:
                print("Connected!")
                await ws.send("hello")
                response = await ws.recv()
                print(f"Response: {response}")
        except Exception as e:
            print(f"Client error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
