#!/usr/bin/env python3
"""Test websockets 14.2 process_request signature."""
import asyncio
import websockets
from websockets.asyncio.server import serve

async def handler(websocket):
    print(f"Handler called! Remote: {websocket.remote_address}")
    print(f"Request path: {websocket.request.path}")
    print(f"Request headers: {dict(websocket.request.headers)}")
    async for msg in websocket:
        print(f"Received: {msg}")
        await websocket.send(f"Echo: {msg}")

async def process_request(connection, request):
    print(f"process_request called!")
    print(f"  connection type: {type(connection)}")
    print(f"  request type: {type(request)}")
    print(f"  request.path: {request.path}")
    print(f"  request.headers: {dict(request.headers)}")
    
    # Check if this is a WebSocket upgrade request
    connection_header = request.headers.get("connection", "").lower()
    upgrade_header = request.headers.get("upgrade", "").lower()
    print(f"  connection: {connection_header}, upgrade: {upgrade_header}")
    
    if "upgrade" in connection_header or upgrade_header == "websocket":
        print("  -> WebSocket upgrade request, returning None")
        return None
    else:
        print("  -> HTTP request, returning response")
        return connection.respond(200, "Server is running\n")

async def main():
    print(f"websockets version: {websockets.__version__}")
    print("Starting server...")
    
    async with serve(handler, "localhost", 9999, process_request=process_request) as server:
        print(f"Server started: {server.sockets}")
        
        await asyncio.sleep(0.5)
        
        print("\nConnecting client...")
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
