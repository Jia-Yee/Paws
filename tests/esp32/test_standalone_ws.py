#!/usr/bin/env python3
"""Standalone WebSocket server test for websockets 16.0."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    try:
        async for msg in websocket:
            print(f"Received: {msg}")
            await websocket.send(f"Echo: {msg}")
    except websockets.ConnectionClosed:
        print("Connection closed")

async def process_request(connection, request):
    print(f"process_request called: path={request.path}")
    print(f"Headers: {dict(request.headers)}")
    
    # 检查是否是 WebSocket 升级请求
    connection_header = request.headers.get("connection", "").lower()
    upgrade_header = request.headers.get("upgrade", "").lower()
    
    print(f"Connection: {connection_header}, Upgrade: {upgrade_header}")
    
    # 如果是 WebSocket 升级请求，返回 None 让握手继续
    if "upgrade" in connection_header or upgrade_header == "websocket":
        print("WebSocket upgrade request, allowing handshake")
        return None
    
    # 否则返回 HTTP 响应
    print("Non-WebSocket request, returning HTTP response")
    return connection.respond(200, "ESP32 Channel is running\n")

async def main():
    print("Starting WebSocket server on ws://localhost:8888")
    server = await serve(
        handler,
        "localhost",
        8888,
        process_request=process_request,
    )
    print("Server started, waiting for connections...")
    
    # 保持服务器运行
    await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
