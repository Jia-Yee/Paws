#!/usr/bin/env python3
"""Test websockets 14.2 process_request API."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def process_request(connection, request):
    print(f"process_request called!")
    print(f"  path: {request.path}")
    print(f"  headers: {dict(request.headers)}")
    
    # 检查是否是 WebSocket 升级请求
    connection_header = request.headers.get("connection", "").lower()
    if connection_header == "upgrade":
        print("  -> WebSocket upgrade, returning None")
        return None
    
    # 返回 HTTP 响应
    print("  -> HTTP request, returning response")
    return connection.respond(200, "Server is running\n")

async def main():
    print(f"websockets version: {websockets.__version__}")
    print("Starting server on ws://localhost:9999...")
    
    async with serve(
        handler,
        "localhost",
        9999,
        process_request=process_request,
    ) as server:
        print(f"Server started: {server.sockets}")
        
        await asyncio.sleep(0.5)
        
        print("\nTest 1: HTTP request")
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:9999/") as resp:
                text = await resp.text()
                print(f"HTTP response: {text.strip()}")
        
        print("\nTest 2: WebSocket request")
        async with websockets.connect("ws://localhost:9999") as ws:
            await ws.send("hello")
            response = await ws.recv()
            print(f"WebSocket response: {response}")

if __name__ == "__main__":
    asyncio.run(main())
