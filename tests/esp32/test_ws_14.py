#!/usr/bin/env python3
"""Test websockets 14.2 API."""
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
    print(f"Headers: {dict(request.headers)}")
    return None

async def main():
    print("Starting server with websockets 14.2...")
    async with serve(handler, "localhost", 9999, process_request=process_request) as server:
        print(f"Server started: {server.sockets}")
        
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
