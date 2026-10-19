#!/usr/bin/env bash
# 停止后台服务（不动 PipeWire 的虚拟音源，下次 run.sh 直接复用）
set -uo pipefail
cd "$(dirname "$0")"
ASR_PORT=${ASR_PORT:-8100}
UI_PORT=${UI_PORT:-8300}

for f in run/asr.pid run/ui.pid run/overlay.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "已停止 $(basename "$f" .pid) ($pid)"
  fi
  rm -f "$f"
done

# wlk 会派生子进程；subtitler 用相对路径启动，pkill 的模式要防自匹配
pkill -f "/.venv/bin/wlk serve" 2>/dev/null
pkill -f "whisper.cpp/build/bin/whisper-server" 2>/dev/null
pkill -f "subtitler[.]py --node" 2>/dev/null
pkill -f "overlay[.]py" 2>/dev/null

port_free() { ! ss -ltn 2>/dev/null | grep -q ":$1 "; }
for p in "$ASR_PORT" "$UI_PORT"; do
  for _ in $(seq 1 20); do
    port_free "$p" && break
    sleep 0.5
  done
  port_free "$p" || echo "警告：端口 $p 仍被占用"
done
echo "完成"
