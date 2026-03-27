# ESP32 MQTT 通道配置指南

## 一、MQTT 通道概述

ESP32 MQTT 通道为 ESP32 设备提供基于 MQTT 协议的通信能力，适用于网络不稳定的场景。

## 二、依赖安装

```bash
# 安装 MQTT 客户端库
pip install paho-mqtt

# 安装 Mosquitto MQTT 代理（可选）
# macOS
brew install mosquitto
brew services start mosquitto

# Ubuntu/Debian
sudo apt install mosquitto
```

## 三、Paws 配置

### 1. 启用 MQTT 通道

编辑 `~/.copaw/config.json`：

```json
{
  "channels": {
    "esp32": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8080
    },
    "esp32-mqtt": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 1883,
      "username": "",
      "password": "",
      "keepalive": 60,
      "topic_prefix": "xiaozhi"
    }
  }
}
```

### 2. 环境变量配置

```bash
# MQTT 通道配置
export ESP32_MQTT_ENABLED=true
export ESP32_MQTT_HOST=0.0.0.0
export ESP32_MQTT_PORT=1883
export ESP32_MQTT_USERNAME=
export ESP32_MQTT_PASSWORD=
export ESP32_MQTT_KEEPALIVE=60
export ESP32_MQTT_TOPIC_PREFIX=xiaozhi

# 语音配置
export ESP32_MQTT_VAD_TYPE=silero
export ESP32_MQTT_ASR_TYPE=funasr
export ESP32_MQTT_TTS_TYPE=edge
```

## 四、ESP32 端配置

### 1. 安装 MQTT 库

使用 ESP-IDF 组件管理器：

```bash
# 在 esp-idf 项目目录
idf.py add-dependency espressif/mqtt
```

### 2. 配置 MQTT 连接

```cpp
// MQTT 配置
#define MQTT_BROKER "mqtt://192.168.3.231:1883"
#define MQTT_CLIENT_ID "esp32-" + MAC_ADDRESS
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""

// MQTT 主题
#define MQTT_TOPIC_SUB "xiaozhi/server/#"
#define MQTT_TOPIC_PUB "xiaozhi/esp32/#"
```

### 3. 示例代码

```cpp
#include "mqtt_client.h"

static esp_mqtt_client_handle_t mqtt_client;

void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            printf("MQTT connected\n");
            esp_mqtt_client_subscribe(mqtt_client, "xiaozhi/server/esp32-001/message", 1);
            break;
        case MQTT_EVENT_DATA:
            printf("Received: %.*s\n", event->data_len, event->data);
            break;
    }
}

void mqtt_init(void) {
    esp_mqtt_client_config_t mqtt_cfg = {
        .uri = MQTT_BROKER,
        .client_id = MQTT_CLIENT_ID,
        .username = MQTT_USERNAME,
        .password = MQTT_PASSWORD,
    };
    
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(mqtt_client);
}

void send_hello(void) {
    char hello_msg[] = "{\"type\": \"hello\", \"device_id\": \"esp32-001\"}";
    esp_mqtt_client_publish(mqtt_client, "xiaozhi/esp32/esp32-001/message", hello_msg, 0, 1, 0);
}
```

## 五、测试 MQTT 通道

### 1. 启动 Paws 服务

```bash
cd /Users/jia/workspace/HardClaw/Paws
source .venv/bin/activate
copaw app --host 0.0.0.0 --port 8000 --reload
```

### 2. 运行 MQTT 测试

```bash
cd /Users/jia/workspace/HardClaw/Paws
source .venv/bin/activate
python test_esp32_mqtt.py
```

### 3. 查看 MQTT 主题

```bash
# 订阅所有 xiaozhi 主题
mosquitto_sub -t "xiaozhi/#" -v

# 发布测试消息
mosquitto_pub -t "xiaozhi/esp32/test-device/message" -m '{"type": "hello"}'
```

## 六、MQTT 主题结构

```
xiaozhi/
├── esp32/            # ESP32 设备发布
│   └── {device_id}/   # 设备 ID
│       └── message    # 消息主题
└── server/           # 服务器发布
    └── {device_id}/   # 设备 ID
        └── message    # 消息主题
```

## 七、消息格式

### 1. HELLO 消息

```json
// ESP32 → Server
{
  "type": "hello",
  "version": 1,
  "features": {"aec": true, "mcp": true},
  "transport": "mqtt",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1
  }
}

// Server → ESP32
{
  "type": "hello",
  "transport": "mqtt",
  "session_id": "xxx",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1
  }
}
```

### 2. 文本消息

```json
// ESP32 → Server
{"type": "text", "text": "你好"}

// Server → ESP32
{"type": "text", "text": "你好，有什么可以帮助你的？"}
```

### 3. 音频消息

```
// 二进制 Opus 音频数据
// 通过 MQTT 发布到 xiaozhi/esp32/{device_id}/message
```

## 八、常见问题

### Q1: MQTT 连接失败

- **检查网络**：确保 ESP32 和 MQTT 代理在同一网络
- **检查端口**：默认 1883 端口是否开放
- **检查认证**：如果启用了认证，确保用户名密码正确

### Q2: 消息丢失

- **启用 QoS**：设置 QoS 为 1 或 2
- **检查网络**：确保网络稳定
- **检查缓冲区**：确保 MQTT 代理有足够的缓冲区

### Q3: 音频传输延迟

- **调整 QoS**：降低 QoS 可以减少延迟
- **压缩音频**：使用 Opus 编码减小数据量
- **优化网络**：确保网络带宽足够

## 九、配置示例

### 完整配置

```json
{
  "channels": {
    "esp32": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8080
    },
    "esp32-mqtt": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 1883,
      "username": "",
      "password": "",
      "keepalive": 60,
      "topic_prefix": "xiaozhi",
      "voice_config": {
        "vad_type": "silero",
        "asr_type": "funasr",
        "tts_type": "edge",
        "sample_rate": 16000
      }
    }
  }
}
```

## 十、监控和调试

### 1. 查看 MQTT 日志

```bash
# 查看 Mosquitto 日志
brew services log mosquitto

# 或直接运行 Mosquitto 查看日志
mosquitto -v
```

### 2. 监控 MQTT 消息

```bash
# 监控所有消息
mosquitto_sub -t "#" -v

# 监控特定设备
mosquitto_sub -t "xiaozhi/#" -v
```

### 3. 测试 MQTT 连接

```bash
# 测试连接
telnet localhost 1883

# 查看 MQTT 代理状态
mosquitto_ctrl status
```

---

## 总结

ESP32 MQTT 通道提供了一种可靠的通信方式，适用于：
- 网络不稳定的环境
- 低功耗设备
- 批量设备管理
- 离线消息缓存

结合 WebSocket 通道，可以为 ESP32 设备提供更全面的通信解决方案。
