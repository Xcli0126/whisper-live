#!/usr/bin/env bash
# 启动：系统音频 -> 英文识别 -> 中文翻译 -> 字幕页
set -uo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
mkdir -p run logs

ASR_PORT=${ASR_PORT:-8100}
UI_PORT=${UI_PORT:-8300}
# CPU 后端用的 faster-whisper 模型：默认 medium > large-v3 > small
if [ -z "${MODEL_DIR:-}" ]; then
  for cand in models/faster-whisper-medium models/faster-whisper-large-v3 models/faster-whisper-small; do
    [ -f "$cand/model.bin" ] && { MODEL_DIR=$cand; break; }
  done
fi
MODEL_DIR=${MODEL_DIR:-models/faster-whisper-medium}
# GPU 后端用的 ggml 模型（whisper.cpp 格式）
GPU_MODEL=${GPU_MODEL:-models/whisper-turbo/ggml-model.bin}
NODE=${NODE:-wlk-sysaudio}
LAN=${LAN:-en}          # 识别语言，auto 可自动判断（多语种）
LANGS=${LANGS:-en,zh}   # 翻译方向：源,目标
case "$LAN" in zh*) LANGS=${LANGS:-$LAN,$LAN} ;; esac  # 源语言是中文时只显示一行
MT=${MT:-local}         # local=纯本地 opus-mt（默认，快、不出网）；auto=本地草稿+云端润色；mymemory=仅云端
case "$MT" in local|argos) export MT_DEBOUNCE=${MT_DEBOUNCE:-0.05} ;; esac   # 纯本地不用等文本稳定
# 延迟档位（配置界面里选）：stable=整句且等云端润色 / balanced=整句+兜底 / fast=半句实时预览
LATENCY=${LATENCY:-balanced}
case "$LATENCY" in
  stable) PUBLISH_DEADLINE=${PUBLISH_DEADLINE:-3.5}; LIVE=0 ;;
  fast)   PUBLISH_DEADLINE=${PUBLISH_DEADLINE:-1.0}; LIVE=1 ;;
  *)      LATENCY=balanced; PUBLISH_DEADLINE=${PUBLISH_DEADLINE:-2.4}; LIVE=0 ;;
esac
export PUBLISH_DEADLINE

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. 虚拟麦克风：把扬声器输出（monitor）伪装成一个录音源 ---
COUNT=$(pw-dump 2>/dev/null | grep -c "node.name\": \"$NODE\"")
if [ "${COUNT:-0}" -ge 1 ]; then
  say "虚拟音源 $NODE 已存在"
  if [ "$COUNT" -gt 2 ]; then
    say "发现重复的 loopback，清理后重建"
    pkill -x pw-loopback 2>/dev/null; sleep 1; COUNT=0
  fi
fi
if [ "${COUNT:-0}" -eq 0 ]; then
  SINK=$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null | sed -n 's/.*node.name = "\([^"]*\)".*/\1/p' | head -1)
  [ -n "$SINK" ] || die "找不到默认输出设备（wpctl inspect @DEFAULT_AUDIO_SINK@ 为空）"
  say "创建虚拟音源 $NODE <- $SINK.monitor"
  setsid nohup pw-loopback \
    --capture-props="node.target=$SINK stream.capture.sink=true" \
    --playback-props="media.class=Audio/Source node.name=$NODE node.description=System-Audio-Monitor" \
    > logs/loopback.log 2>&1 < /dev/null &
  disown
  sleep 1
fi

# --- 2. 识别服务 ---
# ASR_ENGINE=gpu（有显卡时默认）：whisper.cpp + ROCm/HIP，单句约 0.4 秒
# ASR_ENGINE=cpu          ：WhisperLiveKit + faster-whisper 的 CPU int8
if [ -z "${ASR_ENGINE:-}" ]; then
  if [ -x vendor/whisper.cpp/build/bin/whisper-server ] && [ -f "$GPU_MODEL" ]; then
    ASR_ENGINE=gpu
  else
    ASR_ENGINE=cpu
  fi
fi

if curl -sf --max-time 2 "http://127.0.0.1:$ASR_PORT/health" > /dev/null; then
  say "识别服务已在 $ASR_PORT 运行（$ASR_ENGINE）"
elif [ "$ASR_ENGINE" = gpu ]; then
  [ -x vendor/whisper.cpp/build/bin/whisper-server ] || die "whisper-server 没编译：跑 ./build-gpu.sh"
  [ -f "$GPU_MODEL" ] || die "GPU 模型缺失：$GPU_MODEL（跑 ./build-gpu.sh）"
  say "启动识别服务 127.0.0.1:$ASR_PORT（whisper.cpp + 显卡，模型 ${GPU_MODEL##*/}）"
  setsid nohup vendor/whisper.cpp/build/bin/whisper-server \
    -m "$GPU_MODEL" --host 127.0.0.1 --port "$ASR_PORT" -l "$LAN" -nt --convert \
    -bs 1 -bo 1 \
    > logs/asr.log 2>&1 < /dev/null &
  echo $! > run/asr.pid
  disown
  printf '    加载模型中'
  for i in $(seq 1 60); do
    curl -sf --max-time 2 "http://127.0.0.1:$ASR_PORT/health" > /dev/null && { echo " ok"; break; }
    printf '.'; sleep 1
  done
  curl -sf --max-time 2 "http://127.0.0.1:$ASR_PORT/health" > /dev/null || die "识别服务没起来，看 logs/asr.log"
else
  [ -f "$MODEL_DIR/model.bin" ] || die "模型缺失：$MODEL_DIR/model.bin （先跑 ./get-model.sh）"
  say "启动识别服务 127.0.0.1:$ASR_PORT（模型 ${MODEL_DIR##*/}，CPU）"
  setsid nohup .venv/bin/wlk serve \
    --backend faster-whisper \
    --backend-policy localagreement \
    --model_dir "$MODEL_DIR" \
    --lan "$LAN" --pcm-input \
    --host 127.0.0.1 --port "$ASR_PORT" \
    > logs/asr.log 2>&1 < /dev/null &
  echo $! > run/asr.pid
  disown
  printf '    加载模型中'
  for i in $(seq 1 90); do
    curl -sf --max-time 2 "http://127.0.0.1:$ASR_PORT/health" > /dev/null && { echo " ok"; break; }
    printf '.'; sleep 2
  done
  curl -sf --max-time 2 "http://127.0.0.1:$ASR_PORT/health" > /dev/null || die "识别服务没起来，看 logs/asr.log"
fi

# --- 3. 字幕/翻译前端 ---
if curl -sf --max-time 2 "http://127.0.0.1:$UI_PORT/healthz" > /dev/null; then
  say "字幕页已在 $UI_PORT 运行"
else
  if [ "$ASR_ENGINE" = gpu ]; then
    ASR_URL="http://127.0.0.1:$ASR_PORT"
  else
    ASR_URL="ws://127.0.0.1:$ASR_PORT/asr"
  fi
  say "启动字幕页 127.0.0.1:$UI_PORT（识别后端 $ASR_ENGINE）"
  setsid nohup .venv/bin/python subtitler.py --node "$NODE" \
    --asr "$ASR_URL" --asr-kind "$ASR_ENGINE" --port "$UI_PORT" --langs "$LANGS" --engine "$MT" \
    --live-preview "$LIVE" \
    > logs/ui.log 2>&1 < /dev/null &
  echo $! > run/ui.pid
  disown
  for i in $(seq 1 20); do          # 翻译模型加载要 1-3 秒，早查会误报
    curl -sf --max-time 2 "http://127.0.0.1:$UI_PORT/healthz" > /dev/null && break
    sleep 1
  done
  curl -sf --max-time 2 "http://127.0.0.1:$UI_PORT/healthz" > /dev/null \
    || die "字幕页没起来，看 logs/ui.log"
fi

# --- 4. 桌面字幕浮层（Qt6 / XWayland，可盖在全屏播放器上，鼠标穿透） ---
OV=${OV:-1}
if [ "$OV" = 1 ] && [ -x .venv-qt/bin/python ]; then
  if pgrep -f 'overlay[.]py' > /dev/null; then
    say "桌面浮层已在运行"
  else
    say "启动桌面浮层（字幕盖在所有窗口上，鼠标可穿透）"
    setsid nohup env SNAP_URL="http://127.0.0.1:${UI_PORT}/snap" \
      .venv-qt/bin/python overlay.py > logs/overlay.log 2>&1 < /dev/null &
    echo $! > run/overlay.pid
    disown
    sleep 1
    pgrep -f 'overlay[.]py' > /dev/null && say "浮层就绪（OV=0 ./run.sh 可关闭）" \
      || say "浮层没起来，看 logs/overlay.log"
  fi
fi

say "打开字幕页： http://127.0.0.1:$UI_PORT   （识别后端 $ASR_ENGINE，API :$ASR_PORT）"
say "停止： ./stop.sh   状态： ./status.sh   日志： logs/"
