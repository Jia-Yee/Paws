#!/usr/bin/env python3
"""Test websockets 16.0 API."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def process_request(connection, request):
    print(f"process_request called: path={request.path}")
    print(f"Connection header: {request.headers.get('connection', 'N/A')}")
    print(f"Upgrade header: {request.headers.get('upgrade', 'N/A')}")
    # 返回 None 让握手继续
    return None

async def main():
    server = await serve(handler, "localhost", 8888, process_request=process_request)
    print("Test server started on ws://localhost:8888")
    print("Waiting for connections...")
    await asyncio.sleep(60)  # 运行60秒

if __name__ == "__main__":
    asyncio.run(main())
