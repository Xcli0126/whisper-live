#!/usr/bin/env bash
# 检查三层是否都活着
set -uo pipefail
cd "$(dirname "$0")"
ASR_PORT=${ASR_PORT:-8100}
UI_PORT=${UI_PORT:-8300}
NODE=${NODE:-wlk-sysaudio}

chk() { if eval "$2" >/dev/null 2>&1; then echo "  [ok]   $1"; else echo "  [FAIL] $1"; fi; }
echo "系统音频捕获："
chk "虚拟音源 $NODE 存在" "pw-dump | grep -q '\"node.name\": \"$NODE\"'"
chk "默认输出设备可读" "wpctl inspect @DEFAULT_AUDIO_SINK@"
echo "服务："
chk "识别服务 :$ASR_PORT" "curl -sf --max-time 2 http://127.0.0.1:$ASR_PORT/health"
chk "字幕页   :$UI_PORT" "curl -sf --max-time 2 http://127.0.0.1:$UI_PORT/healthz"
if pgrep -f 'overlay[.]py' > /dev/null; then
  echo "  [ok]   桌面浮层 overlay.py（盖在所有窗口上）"
else
  echo "  [warn] 桌面浮层没跑（./run.sh 启动，OV=0 关闭）"
fi
echo "识别后端："
if [ -x vendor/whisper.cpp/build/bin/whisper-server ] && [ -f "${GPU_MODEL:-models/whisper-turbo/ggml-model.bin}" ]; then
  echo "  [ok]   显卡后端可用：$(basename "$(dirname "${GPU_MODEL:-models/whisper-turbo/ggml-model.bin}")")（whisper.cpp + HIP，run.sh 会优先选它）"
  vendor/whisper.cpp/build/bin/whisper-server --help 2>&1 | grep -q "ROCm devices" && echo "  [ok]   ROCm 设备已识别" || echo "  [warn] whisper.cpp 没看见 ROCm 设备，可能只会在 CPU 上跑"
else
  echo "  [warn] 显卡后端未装（跑 ./build-gpu.sh），将使用 CPU"
fi
echo "CPU 模型（默认取 medium > large-v3 > small 中第一个存在的）："
found=0
for m in medium large-v3 small; do
  d="models/faster-whisper-$m"
  [ -f "$d/model.bin" ] || continue
  found=1
  echo "  [ok]   $m  $(du -h "$d/model.bin" | cut -f1)"
done
[ "$found" = 1 ] || echo "  [FAIL] 模型未下载，跑 ./get-model.sh"
echo "翻译引擎："
.venv/bin/python - <<'PY' 2>/dev/null || echo "  [warn] 翻译引擎未就绪"
from pathlib import Path
import os
d = Path(os.path.expanduser("~/.local/share/argos-translate/packages"))
m = Path("models/opus-mt-en-zh")
print("  [ok]   opus-mt en->zh 本地草稿" if (m / "pytorch_model.bin").exists() or (m / "model.bin").exists()
      else "  [warn] opus-mt 缺失，跑 test/dl_opus.sh")
print("  [ok]   argos en->zh 离线兜底" if list(d.glob("translate-en_zh-*")) else "  [warn] argos 未装")
PY
