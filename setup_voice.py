#!/usr/bin/env python3
"""Setup script for voice processing dependencies and models."""
import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd: str, description: str = "") -> bool:
    """Run a shell command and return success status."""
    print(f"\n{'='*60}")
    print(f"📦 {description}")
    print(f"{'='*60}")
    print(f"Running: {cmd}")
    print()
    
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"❌ Failed: {description}")
        return False
    print(f"✅ Success: {description}")
    return True


def check_dependencies():
    """Check if required tools are installed."""
    print("\n" + "="*60)
    print("🔍 Checking dependencies...")
    print("="*60)
    
    dependencies = {
        "python": "python3 --version",
        "pip": "pip --version",
        "ffmpeg": "ffmpeg -version",
    }
    
    missing = []
    for name, cmd in dependencies.items():
        result = subprocess.run(cmd, shell=True, capture_output=True)
        if result.returncode == 0:
            version = result.stdout.decode().split('\n')[0][:50]
            print(f"✅ {name}: {version}")
        else:
            print(f"❌ {name}: not found")
            missing.append(name)
    
    return missing


def install_ffmpeg_mac():
    """Install ffmpeg on macOS using Homebrew."""
    print("\n" + "="*60)
    print("📦 Installing ffmpeg (macOS)")
    print("="*60)
    
    # Check if Homebrew is installed
    result = subprocess.run("brew --version", shell=True, capture_output=True)
    if result.returncode != 0:
        print("❌ Homebrew not found. Please install Homebrew first:")
        print("   /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        return False
    
    return run_command("brew install ffmpeg", "Installing ffmpeg via Homebrew")


def install_python_packages():
    """Install required Python packages."""
    packages = [
        "torch torchaudio",
        "funasr",
        "edge-tts",
        "modelscope",
    ]
    
    print("\n" + "="*60)
    print("📦 Installing Python packages")
    print("="*60)
    
    # Use Tsinghua mirror for faster download in China
    mirror = "-i https://pypi.tuna.tsinghua.edu.cn/simple"
    
    for package in packages:
        if not run_command(f"pip install {package} {mirror}", f"Installing {package}"):
            print(f"⚠️ Failed to install {package}, trying without mirror...")
            run_command(f"pip install {package}", f"Installing {package} (no mirror)")


def download_silero_model():
    """Download Silero VAD model."""
    print("\n" + "="*60)
    print("📦 Downloading Silero VAD model")
    print("="*60)
    
    # Set environment variable for China mirror
    os.environ["TORCH_HUB_MIRROR"] = "china"
    
    try:
        import torch
        
        # Try with mirror first
        print("Downloading from mirror...")
        model, _ = torch.hub.load(
            repo_or_dir="https://ghproxy.com/https://github.com/snakers4/silero-vad",
            model="silero_vad",
            force_reload=True,
            trust_repo=True,
        )
        print("✅ Silero VAD model downloaded successfully!")
        return True
    except Exception as e:
        print(f"❌ Failed to download Silero VAD model: {e}")
        print("\nYou can try manually downloading:")
        print("  export TORCH_HUB_MIRROR=china")
        print("  python -c \"import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad')\"")
        return False


def download_funasr_model():
    """Download FunASR model."""
    print("\n" + "="*60)
    print("📦 Downloading FunASR model")
    print("="*60)
    
    # Set environment variable for ModelScope mirror
    os.environ["MODELSCOPE_CACHE"] = str(Path.home() / ".cache" / "modelscope")
    
    try:
        from funasr import AutoModel
        
        print("Downloading paraformer-zh model (this may take a few minutes)...")
        model = AutoModel(model="paraformer-zh", device="cpu")
        print("✅ FunASR model downloaded successfully!")
        return True
    except ImportError:
        print("❌ FunASR not installed. Run: pip install funasr")
        return False
    except Exception as e:
        print(f"❌ Failed to download FunASR model: {e}")
        print("\nYou can try manually downloading:")
        print("  pip install funasr modelscope")
        print("  python -c \"from funasr import AutoModel; AutoModel(model='paraformer-zh')\"")
        return False


def test_voice_processor():
    """Test the voice processor."""
    print("\n" + "="*60)
    print("🧪 Testing voice processor")
    print("="*60)
    
    try:
        import asyncio
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        
        from copaw.voice import VoiceProcessor, VoiceProcessorConfig
        
        async def test():
            config = VoiceProcessorConfig()
            processor = VoiceProcessor(config=config)
            await processor.initialize()
            
            # Test TTS
            print("Testing TTS...")
            audio = await processor.synthesize_speech("你好")
            print(f"✅ TTS works! Generated {len(audio)} bytes of audio")
            
            return True
        
        return asyncio.run(test())
    except Exception as e:
        print(f"❌ Voice processor test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║          CoPaw Voice Processing Setup Script                 ║
║                                                              ║
║  This script will help you install all dependencies          ║
║  and download required models for voice processing.          ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Check dependencies
    missing = check_dependencies()
    
    # Install ffmpeg if missing (macOS)
    if "ffmpeg" in missing:
        if sys.platform == "darwin":
            install_ffmpeg_mac()
        else:
            print("\n⚠️ Please install ffmpeg manually:")
            print("  Ubuntu/Debian: sudo apt install ffmpeg")
            print("  CentOS/RHEL: sudo yum install ffmpeg")
            print("  Windows: choco install ffmpeg")
    
    # Install Python packages
    print("\n")
    response = input("Install Python packages? [Y/n]: ").strip().lower()
    if response in ("", "y", "yes"):
        install_python_packages()
    
    # Download models
    print("\n")
    response = input("Download Silero VAD model? [Y/n]: ").strip().lower()
    if response in ("", "y", "yes"):
        download_silero_model()
    
    print("\n")
    response = input("Download FunASR model? [Y/n]: ").strip().lower()
    if response in ("", "y", "yes"):
        download_funasr_model()
    
    # Test
    print("\n")
    response = input("Test voice processor? [Y/n]: ").strip().lower()
    if response in ("", "y", "yes"):
        test_voice_processor()
    
    print("""
╔══════════════════════════════════════════════════════════════╗
║                    Setup Complete!                           ║
║                                                              ║
║  To use China mirrors for model download, set:               ║
║    export TORCH_HUB_MIRROR=china                             ║
║                                                              ║
║  To use China mirrors for pip, use:                          ║
║    pip install <package> -i https://pypi.tuna.tsinghua.edu.cn/simple
║                                                              ║
║  For more help, see:                                         ║
║    https://github.com/snakers4/silero-vad                    ║
║    https://github.com/alibaba-damo-academy/FunASR            ║
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
