#!/usr/bin/env bash
# 从 ModelScope 下载 faster-whisper 模型（HuggingFace 在本机不可达）
#   ./get-model.sh                                       -> large-v3（默认）
#   REPO=gpustack/faster-whisper-medium ./get-model.sh   -> medium（日常推荐）
#   REPO=gpustack/faster-whisper-small  ./get-model.sh   -> small（最快）
#   目录默认 models/<仓库名>，可用 DEST=... 覆盖
set -uo pipefail
cd "$(dirname "$0")"
REPO=${REPO:-keepitsimple/faster-whisper-large-v3}
DEST=${DEST:-models/${REPO##*/}}
BASE="https://www.modelscope.cn/api/v1/models/${REPO}/repo?Revision=master&FilePath="
mkdir -p "$DEST"
echo "仓库 $REPO -> $DEST"

fetch() {   # fetch <仓库内文件名> [保存名]；ModelScope 对不存在的文件返回 200 + JSON 错误体
  local name=$1 out=${2:-$1}
  [ -s "$DEST/$out" ] && { echo "skip $out"; return 0; }
  echo "下载 $out"
  curl -sL -C - --retry 5 --retry-delay 3 -o "$DEST/$out" "${BASE}$name" || { rm -f "$DEST/$out"; return 1; }
  if grep -q '"Success":false' "$DEST/$out" 2>/dev/null; then rm -f "$DEST/$out"; return 1; fi
}

for f in config.json tokenizer.json model.bin; do fetch "$f" || { echo "失败: $f"; exit 1; }; done
fetch vocabulary.json || fetch vocabulary.txt || { echo "失败: 词表"; exit 1; }
echo "完成：$DEST ($(du -sh "$DEST" | cut -f1))"
