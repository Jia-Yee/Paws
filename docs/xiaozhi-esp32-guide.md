# xiaozhi-esp32 使用指南

## 一、简介

xiaozhi-esp32 是一个运行在 ESP32 系列芯片上的 AI 语音助手客户端，通过 WebSocket 与服务器通信，支持语音识别、语音合成和物联网控制。

## 二、硬件要求

| 项目 | 要求 |
|------|------|
| **芯片** | ESP32-S3 (推荐) / ESP32 / ESP32-C3 / ESP32-S2 |
| **PSRAM** | 推荐 8MB (语音处理需要) |
| **Flash** | 16MB (推荐) |
| **麦克风** | I2S 数字麦克风 (如 INMP441) |
| **扬声器** | I2S 功放 + 扬声器 (如 MAX98357A) |

## 三、安装步骤

### 3.1 环境准备

```bash
# 安装 ESP-IDF (推荐 v5.0+)
# macOS / Linux
git clone --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
. ./export.sh

# 或使用 PlatformIO (更简单)
pip install platformio
```

### 3.2 克隆项目

```bash
# GitHub (官方)
git clone https://github.com/78/xiaozhi-esp32.git
cd xiaozhi-esp32

# 或使用国内镜像
git clone https://gitee.com/78/xiaozhi-esp32.git
cd xiaozhi-esp32
```

### 3.3 配置项目

```bash
# 设置目标芯片
idf.py set-target esp32s3

# 打开配置菜单
idf.py menuconfig
```

**关键配置项：**

```
Component config → 
  → ESP32S3-Specific → 
    → CPU frequency (240MHz)
    → PSRAM (OPI/QSPI)

Component config → 
  → Wi-Fi Provisioning Manager → 
    → 启用 WiFi 配网

Audio → 
  → Audio Board → 
    → 选择你的开发板型号
```

### 3.4 编译和烧录

```bash
# 编译
idf.py build

# 烧录 (替换 /dev/ttyUSB0 为你的串口)
idf.py -p /dev/ttyUSB0 flash

# 查看日志
idf.py -p /dev/ttyUSB0 monitor
```

### 3.5 使用 PlatformIO (推荐新手)

```bash
# 安装 PlatformIO
pip install platformio

# 克隆项目
git clone https://github.com/78/xiaozhi-esp32.git
cd xiaozhi-esp32

# 编译和烧录
pio run -t upload

# 查看日志
pio device monitor
```

## 四、连接 Paws 服务器

### 4.1 服务器配置

确保 Paws 的 ESP32 Channel 已启用：

```json
// ~/.copaw/config.json
{
  "channels": {
    "esp32": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8080
    }
  }
}
```

### 4.2 ESP32 配置服务器地址

**方式 1：通过配置界面**

在 ESP32 启动后，进入配置模式，设置：
- 服务器地址：`ws://你的电脑IP:8080`
- Token：留空或自定义

**方式 2：修改代码**

编辑 `main/settings.h` 或相关配置文件：

```c
#define SERVER_URL "ws://192.168.1.100:8080"
#define SERVER_TOKEN ""
```

### 4.3 WiFi 配网

首次启动时，ESP32 会进入配网模式：

1. 手机连接 WiFi 热点 `Xiaozhi-XXXX`
2. 打开浏览器访问 `192.168.4.1`
3. 选择你的 WiFi 并输入密码
4. 配置服务器地址

## 五、通信协议

### 5.1 连接流程

```
ESP32                              Paws Server
   |                                    |
   |-------- WebSocket 连接 ----------->|
   |         Headers:                   |
   |           Device-Id: MAC地址       |
   |           Client-Id: UUID          |
   |                                    |
   |-------- HELLO 消息 --------------->|
   |<------- HELLO 响应 ----------------|
   |                                    |
   |-------- LISTEN start ------------->|
   |                                    |
   |-------- 音频数据 (Opus) ---------->|
   |<------- TTS start -----------------|
   |<------- 音频数据 (Opus) -----------|
   |<------- TTS stop ------------------|
   |                                    |
```

### 5.2 消息格式

#### HELLO 握手

```json
// ESP32 -> Server
{
  "type": "hello",
  "version": 1,
  "features": {"aec": true, "mcp": true},
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}

// Server -> ESP32
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "xxx",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1
  }
}
```

#### 录音控制

```json
// 开始录音
{"type": "listen", "state": "start", "mode": "auto"}

// 停止录音
{"type": "listen", "state": "stop"}

// 唤醒词检测
{"type": "listen", "state": "detect", "text": "你好小明"}
```

#### TTS 控制

```json
// 开始播放
{"type": "tts", "state": "start"}

// 句子开始
{"type": "tts", "state": "sentence_start", "text": "你好"}

// 停止播放
{"type": "tts", "state": "stop"}
```

#### 音频数据

```
二进制 Opus 编码音频数据
- 采样率: 16000 Hz
- 声道: 1 (单声道)
- 帧时长: 60ms
- 每帧样本数: 960
```

## 六、硬件接线

### 6.1 ESP32-S3 + INMP441 (麦克风) + MAX98357A (功放)

```
ESP32-S3           INMP441          MAX98357A
--------           -------          ---------
3.3V    ---------> VDD
GND     ---------> GND   ---------> GND
GPIO4   ---------> SCK
GPIO5   ---------> WS
GPIO6   ---------> SD
GPIO7   -------------------------> BCLK
GPIO8   -------------------------> LRC
GPIO9   -------------------------> DIN
5V      -------------------------> VIN
```

### 6.2 推荐开发板

| 开发板 | 特点 | 价格 |
|--------|------|------|
| ESP32-S3-Korvo-1 | 官方语音开发板 | ¥150+ |
| ESP32-S3-BOX | 带屏幕和外壳 | ¥200+ |
| 乐鑫 AI 开发板 | 集成麦克风扬声器 | ¥100+ |
| 自行组装 | 灵活，成本低 | ¥50-100 |

## 七、常见问题

### Q1: 编译报错内存不足

```bash
# 启用 PSRAM
idf.py menuconfig
# Component config → ESP32S3-Specific → SPI RAM
```

### Q2: WiFi 连接失败

```bash
# 检查 WiFi 信号
# 确保 2.4GHz 网络 (ESP32 不支持 5GHz)
```

### Q3: 音频质量差

```bash
# 调整增益
# 检查麦克风接线
# 使用屏蔽线减少干扰
```

### Q4: 无法连接服务器

```bash
# 检查防火墙
# 确保 IP 地址正确
# 检查端口是否开放
```

## 八、相关链接

| 资源 | 链接 |
|------|------|
| GitHub 仓库 | https://github.com/78/xiaozhi-esp32 |
| ESP-IDF 文档 | https://docs.espressif.com/projects/esp-idf/ |
| PlatformIO | https://platformio.org/ |
| Paws 项目 | https://github.com/your-repo/paws |

## 九、快速测试

使用 Python 测试脚本模拟 ESP32：

```bash
cd /Users/jia/workspace/HardClaw/Paws
source .venv/bin/activate

# 测试 Xiaozhi 协议
python test_xiaozhi_protocol.py

# 测试基础功能
python test_esp32_client.py --mode ping
python test_esp32_voice_full.py --mode voice
```
