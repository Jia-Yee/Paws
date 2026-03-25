#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test HTTP connection to ESP32 Channel."""
import http.client
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_http_connection():
    """Test HTTP connection to the server."""
    logger.info("Testing HTTP connection to localhost:8080")
    
    try:
        conn = http.client.HTTPConnection("localhost", 8080)
        conn.request("GET", "/")
        response = conn.getresponse()
        logger.info(f"HTTP Status: {response.status}")
        logger.info(f"HTTP Reason: {response.reason}")
        data = response.read()
        logger.info(f"Response: {data.decode('utf-8')}")
        conn.close()
    except Exception as e:
        logger.error(f"HTTP connection error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_http_connection()
