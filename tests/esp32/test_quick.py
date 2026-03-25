#!/usr/bin/env python3
"""Quick test for ESP32 Channel."""
import asyncio
import websockets
import json

async def test():
    uri = 'ws://localhost:8080'
    headers = {'device-id': 'test-001'}
    
    print('Connecting...')
    async with websockets.connect(uri, additional_headers=headers) as ws:
        print('Connected!')
        
        # Send HELLO
        hello = json.dumps({'type': 'hello', 'device_id': 'test-001', 'version': '1.0'})
        print(f'Sending: {hello}')
        await ws.send(hello)
        
        # Wait for response with timeout
        print('Waiting for response...')
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            print(f'Response: {response}')
        except asyncio.TimeoutError:
            print('Timeout waiting for response')

if __name__ == "__main__":
    asyncio.run(test())
