#!/usr/bin/env python3
"""Test ESP32 MQTT Channel."""
import argparse
import asyncio
import json
import logging
import time

import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class MQTTTestClient:
    """MQTT test client to simulate ESP32 device."""
    
    def __init__(self, broker, port, device_id):
        self.broker = broker
        self.port = port
        self.device_id = device_id
        self.client = mqtt.Client(client_id=f"esp32-test-{device_id}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.connected = False
        self.received_messages = []
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"Connected to MQTT broker: {self.broker}:{self.port}")
            self.connected = True
            # Subscribe to server responses
            topic = f"xiaozhi/server/{self.device_id}/message"
            self.client.subscribe(topic)
            logger.info(f"Subscribed to topic: {topic}")
        else:
            logger.error(f"Failed to connect: {rc}")
    
    def on_message(self, client, userdata, msg):
        """Callback when message is received."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        logger.info(f"Received from {topic}: {payload[:100]}...")
        self.received_messages.append((topic, payload))
    
    def connect(self):
        """Connect to MQTT broker."""
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        # Wait for connection
        for _ in range(10):
            if self.connected:
                break
            time.sleep(0.5)
        return self.connected
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()
    
    def publish(self, message, topic=None):
        """Publish message to broker."""
        if topic is None:
            topic = f"xiaozhi/esp32/{self.device_id}/message"
        logger.info(f"Publishing to {topic}: {message[:100]}...")
        self.client.publish(topic, message, qos=1)


async def test_mqtt_connection(broker, port, device_id):
    """Test MQTT connection."""
    client = MQTTTestClient(broker, port, device_id)
    
    if not client.connect():
        logger.error("Failed to connect to MQTT broker")
        return False
    
    try:
        # Test 1: HELLO message
        logger.info("=" * 50)
        logger.info("Test 1: HELLO message")
        logger.info("=" * 50)
        hello_msg = {
            "type": "hello",
            "version": 1,
            "features": {"aec": True, "mcp": True},
            "transport": "mqtt",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        client.publish(json.dumps(hello_msg))
        await asyncio.sleep(2)
        
        # Test 2: PING message
        logger.info("\n" + "=" * 50)
        logger.info("Test 2: PING message")
        logger.info("=" * 50)
        ping_msg = {"type": "ping", "timestamp": int(time.time() * 1000)}
        client.publish(json.dumps(ping_msg))
        await asyncio.sleep(2)
        
        # Test 3: TEXT message
        logger.info("\n" + "=" * 50)
        logger.info("Test 3: TEXT message")
        logger.info("=" * 50)
        text_msg = {"type": "text", "text": "你好"}
        client.publish(json.dumps(text_msg))
        await asyncio.sleep(5)
        
        # Test 4: LISTEN message
        logger.info("\n" + "=" * 50)
        logger.info("Test 4: LISTEN message")
        logger.info("=" * 50)
        listen_msg = {"type": "listen", "state": "start", "mode": "auto"}
        client.publish(json.dumps(listen_msg))
        await asyncio.sleep(2)
        
        # Summary
        logger.info("\n" + "=" * 50)
        logger.info("Test Summary")
        logger.info("=" * 50)
        logger.info(f"Received {len(client.received_messages)} messages")
        for i, (topic, payload) in enumerate(client.received_messages):
            logger.info(f"  {i+1}. {topic}: {payload[:100]}...")
        
        return len(client.received_messages) > 0
        
    finally:
        client.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="Test ESP32 MQTT Channel")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--device-id", default="test-device-001", help="Device ID")
    
    args = parser.parse_args()
    
    success = await test_mqtt_connection(args.broker, args.port, args.device_id)
    
    if success:
        logger.info("\n✅ MQTT test passed!")
    else:
        logger.error("\n❌ MQTT test failed!")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
