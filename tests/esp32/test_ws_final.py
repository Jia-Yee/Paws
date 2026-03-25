#!/usr/bin/env python3
"""Test websockets 15.0.1 with correct approach."""
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
    
    # 使用 async with 作为上下文管理器
    async with serve(handler, "localhost", 9999) as server:
        print(f"Server sockets: {server.sockets}")
        
        # 给服务器一点时间完全启动
        await asyncio.sleep(0.5)
        
        print("Connecting client...")
        try:
            async with websockets.connect("ws://localhost:9999") as ws:
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
