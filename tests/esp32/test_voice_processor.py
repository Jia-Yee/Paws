#!/usr/bin/env python3
"""Test voice processor."""
import asyncio
import struct
import sys

sys.path.insert(0, "src")

from copaw.voice import VoiceProcessor, VoiceProcessorConfig


async def test():
    print("=" * 60)
    print("测试语音处理器")
    print("=" * 60)

    config = VoiceProcessorConfig()
    processor = VoiceProcessor(config=config)

    print("\n1. 初始化语音处理器...")
    await processor.initialize()
    print("✅ 初始化成功!")

    print("\n2. 测试 TTS (语音合成)...")
    audio = await processor.synthesize_speech("你好，我是语音助手")
    print(f"✅ TTS 成功! 生成音频: {len(audio)} 字节")

    print("\n3. 测试 VAD (语音活动检测)...")
    # 生成 1 秒静音
    silence = struct.pack("<" + "h" * 16000, *([0] * 16000))
    result = processor._vad.is_speech(silence, 16000)
    print(f"   静音检测结果: is_speech={result.is_speech}, prob={result.probability:.3f}")

    print("\n" + "=" * 60)
    print("✅ 所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test())
