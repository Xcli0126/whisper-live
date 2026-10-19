#!/usr/bin/env bash
# 装 GPU（ROCm/HIP）这一路：编译 whisper.cpp + 下载 whisper-large-v3-turbo + 转成 ggml
# 只需要跑一次；CPU 版不依赖它。任何一步失败都不会动现有 CPU 环境。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p vendor models logs

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

command -v cmake > /dev/null || die "缺 cmake（sudo apt install cmake）"
command -v git > /dev/null || die "缺 git"

# --- 1. 认显卡架构（gfx1151 / gfx1103 ...）---
ARCH=${AMDGPU_TARGETS:-$(rocminfo 2>/dev/null | sed -n 's/^ *Name: *\(gfx[0-9a-z]*\)$/\1/p' | head -1)}
[ -n "$ARCH" ] || ARCH=gfx1151
say "编译目标 $ARCH"

# --- 2. whisper.cpp（带 HIP 后端）---
if [ ! -d vendor/whisper.cpp ]; then
  say "拉 whisper.cpp"
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git vendor/whisper.cpp
fi
cd vendor/whisper.cpp
if [ ! -x build/bin/whisper-server ]; then
  say "cmake 配置（GGML_HIP=ON）"
  cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS="$ARCH" -DCMAKE_BUILD_TYPE=Release -DWHISPER_SDL2=OFF
  say "编译（16 线程，约 5-10 分钟）"
  cmake --build build -j "$(nproc)"
fi
build/bin/whisper-cli --help > /dev/null 2>&1 || die "whisper-cli 没编出来"
say "whisper.cpp 编译完成：$(ls build/bin/whisper-server)"
cd ../..

# --- 3. 权重：ModelScope 上的 whisper-large-v3-turbo（HF 在国内不可达）---
if [ ! -f models/whisper-turbo/ggml-model.bin ]; then
  HF=models/whisper-turbo-hf
  mkdir -p "$HF"
  BASE="https://www.modelscope.cn/api/v1/models/unsloth/whisper-large-v3-turbo/repo?Revision=master&FilePath="
  for f in config.json generation_config.json preprocessor_config.json tokenizer.json \
           tokenizer_config.json added_tokens.json normalizer.json merges.txt vocab.json \
           special_tokens_map.json model.safetensors; do
    [ -s "$HF/$f" ] && continue
    say "下载 $f"
    curl -fL --retry 5 -o "$HF/$f" "${BASE}$f" || die "下载失败：$f"
  done
  # 转换脚本要 openai-whisper 的 mel 滤波器，包里自带一份
  mkdir -p vendor/whisper.cpp/whisper/assets
  cp .venv/lib/python3.12/site-packages/whisperlivekit/whisper/assets/mel_filters.npz \
     vendor/whisper.cpp/whisper/assets/
  say "转 ggml（1.6 GB，约 1 分钟）"
  mkdir -p models/whisper-turbo
  .venv/bin/python vendor/whisper.cpp/models/convert-h5-to-ggml.py "$HF" vendor/whisper.cpp models/whisper-turbo
fi
[ -f models/whisper-turbo/ggml-model.bin ] || die "ggml 模型没生成"
say "模型就绪：models/whisper-turbo/ggml-model.bin（$(du -h models/whisper-turbo/ggml-model.bin | cut -f1)）"

# --- 4. 自检：跑一段自带样例 ---
say "自检：转写 samples/jfk.wav"
vendor/whisper.cpp/build/bin/whisper-cli -m models/whisper-turbo/ggml-model.bin \
  -f vendor/whisper.cpp/samples/jfk.wav -l en -nt -np 2>&1 | tail -3
say "完成，接着跑 ./run.sh（会自动选 GPU 后端）"
