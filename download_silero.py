#!/usr/bin/env python3
"""Download Silero VAD model from HuggingFace mirror."""
import os
import sys
from pathlib import Path
from urllib.request import urlretrieve
import ssl

# 忽略 SSL 证书验证（某些镜像需要）
ssl._create_default_https_context = ssl._create_unverified_context

# 模型保存路径
MODEL_DIR = Path.home() / ".cache" / "copaw" / "models"
MODEL_PATH = MODEL_DIR / "silero_vad.jit"

# 下载 URL 列表（按优先级排序）
URLS = [
    # HuggingFace 镜像（国内）
    "https://hf-mirror.com/snakers4/silero-vad/resolve/master/files/silero_vad.jit",
    # GitHub 直连
    "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit",
    # 其他镜像
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/files/silero_vad.jit",
]


def download_progress(block_num, block_size, total_size):
    """显示下载进度。"""
    downloaded = block_num * block_size
    percent = min(100, downloaded * 100 // total_size) if total_size > 0 else 0
    bar_len = 40
    filled_len = int(bar_len * percent // 100)
    bar = "=" * filled_len + "-" * (bar_len - filled_len)
    print(f"\r[{bar}] {percent}% ({downloaded // 1024}KB / {total_size // 1024}KB)", end="", flush=True)


def download_model():
    """下载模型。"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    
    # 如果已存在，询问是否重新下载
    if MODEL_PATH.exists():
        size = MODEL_PATH.stat().st_size
        if size > 1_000_000:  # 模型大约 1.8MB
            print(f"✅ 模型已存在: {MODEL_PATH}")
            print(f"   文件大小: {size / 1024:.1f} KB")
            return True
        else:
            print(f"⚠️ 模型文件不完整 ({size} bytes)，重新下载...")
            MODEL_PATH.unlink()
    
    print("=" * 60)
    print("下载 Silero VAD 模型")
    print("=" * 60)
    
    for i, url in enumerate(URLS, 1):
        print(f"\n[{i}/{len(URLS)}] 尝试: {url}")
        try:
            urlretrieve(url, MODEL_PATH, download_progress)
            print()  # 换行
            
            # 验证文件大小
            size = MODEL_PATH.stat().st_size
            if size > 1_000_000:  # 模型大约 1.8MB
                print(f"✅ 下载成功!")
                print(f"   保存路径: {MODEL_PATH}")
                print(f"   文件大小: {size / 1024:.1f} KB")
                return True
            else:
                print(f"❌ 文件不完整 ({size} bytes)")
                MODEL_PATH.unlink()
                
        except Exception as e:
            print(f"\n❌ 下载失败: {e}")
            if MODEL_PATH.exists():
                MODEL_PATH.unlink()
            continue
    
    print("\n" + "=" * 60)
    print("❌ 所有下载源都失败了")
    print("=" * 60)
    print("\n请手动下载模型:")
    print(f"  1. 访问: https://hf-mirror.com/snakers4/silero-vad/tree/master/files")
    print(f"  2. 下载 silero_vad.jit 文件")
    print(f"  3. 保存到: {MODEL_PATH}")
    return False


def verify_model():
    """验证模型是否可以加载。"""
    print("\n" + "=" * 60)
    print("验证模型")
    print("=" * 60)
    
    try:
        import torch
        print(f"加载模型: {MODEL_PATH}")
        model = torch.jit.load(str(MODEL_PATH))
        print("✅ 模型加载成功!")
        return True
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        return False


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║          Silero VAD 模型下载工具                              ║
║                                                              ║
║  使用国内 HuggingFace 镜像下载                                ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    if download_model():
        verify_model()
