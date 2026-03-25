# xiaozhi-esp32 与 Paws ESP32 Channel 对接分析报告

## 一、兼容性总结

| 层级 | 兼容性 | 说明 |
|------|--------|------|
| 连接层 | ✅ 兼容 | WebSocket 协议、认证头都支持 |
| 握手层 | ⚠️ 部分兼容 | 字段名有差异，需要适配 |
| 音频层 | ❌ 不兼容 | xiaozhi 用 Opus，Paws 用 PCM |
| 消息层 | ⚠️ 部分兼容 | 缺少部分消息类型 |

## 二、必须修改

### 2.1 音频编码格式（关键）

**问题**: xiaozhi-esp32 使用 Opus 编码，Paws 使用 PCM 格式

**解决方案**: 
1. 添加 Opus 编解码器支持
2. 或在 ESP32 端修改为 PCM 格式

```python
# 方案 A: 在 Paws 添加 Opus 支持
pip install opuslib  # 或 pyogg

# 方案 B: ESP32 端发送 PCM（需要修改 ESP32 固件）
audio_params.format = "pcm"
```

### 2.2 HELLO 消息适配

**xiaozhi-esp32 发送**:
```json
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
```

**Paws 需要适配**:
- `audio_params` → `audio_config`
- 解析 `features` 和 `transport`
- `version` 支持数字类型

### 2.3 添加 listen 消息处理

```json
// 开始录音
{"type": "listen", "state": "start"}

// 停止录音  
{"type": "listen", "state": "stop"}

// 唤醒词检测
{"type": "listen", "state": "detect"}
```

## 三、建议添加

### 3.1 TTS 控制消息

```json
// 开始播放
{"type": "tts", "state": "start"}

// 停止播放
{"type": "tts", "state": "stop"}
```

### 3.2 STT 结果消息

```json
{
    "type": "stt",
    "text": "识别的文本",
    "is_final": true
}
```

### 3.3 LLM 表情消息

```json
{
    "type": "llm", 
    "emotion": "happy",
    "text": "表情文本"
}
```

## 四、完整对接方案

### 方案 A: Paws 适配 xiaozhi-esp32（推荐）

1. **添加 Opus 编解码支持**
   ```bash
   pip install opuslib
   ```

2. **修改 protocol.py 解析**
   - 支持 `audio_params` 字段名
   - 支持 `features` 和 `transport` 字段
   - 支持 `listen` 消息类型

3. **修改 channel.py 处理**
   - 添加 Opus 解码处理
   - 添加 `listen` 消息处理
   - 添加 TTS 控制消息发送

### 方案 B: ESP32 端修改

1. 修改音频格式为 PCM
2. 修改 HELLO 消息字段名
3. 简化消息类型

## 五、优先级建议

| 优先级 | 修改项 | 工作量 | 影响 |
|--------|--------|--------|------|
| P0 | Opus 编解码支持 | 中 | 核心功能 |
| P0 | HELLO 字段适配 | 小 | 连接必须 |
| P1 | listen 消息处理 | 小 | 录音控制 |
| P1 | TTS 控制消息 | 小 | 播放控制 |
| P2 | STT 结果消息 | 小 | 用户体验 |
| P2 | LLM 表情消息 | 小 | 用户体验 |
| P3 | MCP 协议支持 | 大 | 物联网扩展 |
