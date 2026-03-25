#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Raw WebSocket Test Client."""
import socket
import ssl
import base64
import hashlib
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_websocket_connection(host, port, path):
    """Create WebSocket connection using raw socket."""
    # Create TCP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    
    # WebSocket handshake
    key = base64.b64encode(b"test-key").decode('utf-8')
    handshake = f"""GET {path} HTTP/1.1
"
"Host: {host}:{port}
"
"Upgrade: websocket
"
"Connection: Upgrade
"
"Sec-WebSocket-Key: {key}
"
"Sec-WebSocket-Version: 13
"
"Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits
"
"Sec-WebSocket-Protocol: chat
"
"
"""
    
    logger.info("Sending handshake")
    sock.send(handshake.encode('utf-8'))
    
    # Receive response
    response = sock.recv(1024).decode('utf-8')
    logger.info(f"Received response: {response}")
    
    # Check if handshake was successful
    if "101 Switching Protocols" in response:
        logger.info("WebSocket handshake successful!")
        return sock
    else:
        logger.error("WebSocket handshake failed!")
        sock.close()
        return None

def main():
    """Main function."""
    host = "127.0.0.1"
    port = 8080
    path = "/?device-id=test-device-001"
    
    logger.info(f"Connecting to ws://{host}:{port}{path}")
    
    sock = create_websocket_connection(host, port, path)
    if sock:
        logger.info("Connection established!")
        sock.close()
    else:
        logger.error("Connection failed!")

if __name__ == "__main__":
    main()
