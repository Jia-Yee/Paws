# Paws

## 项目简介
Paws 是一个基于 CoPaw 框架开发的智能语音助手项目，专注于硬件控制和对物理世界的影响。它扩展了 CoPaw 的核心功能，增加了设备连接和硬件交互能力，让 AI 助手能够与现实世界进行互动。

## 与 CoPaw 的关系
Paws 基于 CoPaw 框架开发，继承了 CoPaw 的核心能力，并增加了硬件控制功能：
- **CoPaw**: 提供核心的 AI 助手功能，支持多通道集成和智能对话
- **Paws**: 扩展了硬件控制能力，支持 ESP32 等设备连接，实现对物理世界的影响

## CoPaw 框架
CoPaw (Conversational Paw) - "Works for you, grows with you."

Your Personal AI Assistant; easy to install, deploy on your own machine or on the cloud; supports multiple chat apps with easily extensible capabilities.

### Core capabilities:

- **Every channel** — DingTalk, Feishu, QQ, Discord, iMessage, and more. One assistant, connect as you need.
- **Under your control** — Memory and personalization under your control. Deploy locally or in the cloud; scheduled reminders to any channel.
- **Multi-Agent** — Create multiple independent agents, each with their own specialty; enable collaboration skill for inter-agent communication.
- **Skills** — Built-in cron; custom skills in your workspace, auto-loaded. No lock-in.

## Paws 扩展功能

### 硬件控制
- **ESP32 支持**：通过 WebSocket 和 MQTT 协议连接 ESP32 设备
- **语音交互**：实现完整的语音对话流程
- **设备状态管理**：支持设备的监听、说话、空闲等状态
- **实时通信**：低延迟的设备控制和状态反馈

### 语音处理
- **语音活动检测 (VAD)**：使用 Silero VAD 模型检测语音开始和结束
- **自动语音识别 (ASR)**：使用 FunASR 进行高效准确的语音转文本
- **文本转语音 (TTS)**：使用 Edge TTS 生成自然流畅的语音

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

## What you can do

- **Voice control**：Use voice commands to control ESP32 devices
- **Smart home integration**：Connect and control smart home devices
- **Real-time monitoring**：Monitor device status and environment data
- **Automated responses**：Set up automated responses to voice commands
- **Custom skills**：Extend functionality with custom skills
- **Multi-device coordination**：Coordinate multiple devices for complex tasks

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
- 智能家电

## 贡献指南
欢迎提交 Issue 和 Pull Request 来帮助改进项目。

## 感谢 (Thanks)
- 感谢 CoPaw 框架提供的核心 AI 助手功能
- 感谢 xiaozhi-esp32-server 项目提供的参考实现
- 感谢 Silero VAD 提供的语音活动检测模型
- 感谢 FunASR 提供的自动语音识别服务
- 感谢 Edge TTS 提供的文本转语音服务
- 感谢所有为项目做出贡献的开发者

## 许可证
MIT License
