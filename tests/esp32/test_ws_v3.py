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

async def process_request(connection, request):
    print(f"process_request: path={request.path}")
    print(f"Headers: connection={request.headers.get('connection')}, upgrade={request.headers.get('upgrade')}")
    return None

async def main():
    print("Starting WebSocket server on ws://localhost:9999")
    async with serve(handler, "localhost", 9999, process_request=process_request) as server:
        print("Server started, testing connection...")
        
        # 在同一个事件循环中测试
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
        
        # 关闭服务器
        server.close()
        await server.wait_closed()
        print("Server closed")

if __name__ == "__main__":
    asyncio.run(main())
