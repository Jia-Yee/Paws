#!/usr/bin/env python3
"""Test websockets 16.0 - correct approach."""
import asyncio
import websockets
from websockets.asyncio.server import serve

# 全局变量来传递服务器状态
server_ready = asyncio.Event()

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def server_task():
    """Run the server."""
    async with serve(handler, "localhost", 9999) as server:
        print("Server started on ws://localhost:9999")
        server_ready.set()
        # 保持服务器运行直到被取消
        await asyncio.Future()

async def client_task():
    """Run the client after server is ready."""
    await server_ready.wait()
    print("Connecting to server...")
    try:
        async with websockets.connect("ws://localhost:9999") as ws:
            print("Connected!")
            await ws.send("hello")
            response = await ws.recv()
            print(f"Response: {response}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

async def main():
    # 同时运行服务器和客户端
    server = asyncio.create_task(server_task())
    
    # 等待一小段时间确保服务器完全启动
    await asyncio.sleep(0.5)
    
    # 运行客户端
    await client_task()
    
    # 取消服务器
    server.cancel()
    try:
        await server
    except asyncio.CancelledError:
        pass
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
