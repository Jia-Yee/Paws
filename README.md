# Paws

## 项目简介
Paws 是一个智能语音助手项目，支持多种设备连接和语音交互。

## 新闻动态 (News)

### 2026-03-27
- **新增 ESP32 Channel 支持**
  - 实现了完整的 ESP32 WebSocket 对话流程
  - 支持 MQTT 协议连接
  - 集成了 VAD (语音活动检测) 功能
  - 实现了 ASR (自动语音识别) 和 TTS (文本转语音)
  - 支持完整的对话流程：唤醒词检测 → 语音识别 → AI 处理 → 语音回复
  - 优化了长时间空闲状态的处理

### 2026-03-27
- **Added ESP32 Channel Support**
  - Implemented complete ESP32 WebSocket dialogue flow
  - Supported MQTT protocol connection
  - Integrated VAD (Voice Activity Detection) functionality
  - Implemented ASR (Automatic Speech Recognition) and TTS (Text-to-Speech)
  - Supported complete dialogue flow: wake word detection → speech recognition → AI processing → voice response
  - Optimized handling of long idle states

## 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行服务器
```bash
copaw app --host 0.0.0.0 --port 8000 --reload
```

## 支持的设备
- ESP32 (WebSocket/MQTT)
- 其他语音设备

## 功能特性
- 语音识别
- 文本转语音
- 智能对话
- 多设备支持
- 实时通信

## 贡献指南
欢迎提交 Issue 和 Pull Request 来帮助改进项目。

## 感谢 (Thanks)
- 感谢 xiaozhi-esp32-server 项目提供的参考实现
- 感谢 Silero VAD 提供的语音活动检测模型
- 感谢 FunASR 提供的自动语音识别服务
- 感谢 Edge TTS 提供的文本转语音服务
- 感谢所有为项目做出贡献的开发者

## 许可证
MIT License
