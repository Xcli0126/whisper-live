# Troubleshooting

Start with `./status.sh`. It checks the five things that break most often and prints a line per
check, so the failing section tells you where to look next.

```text
== 系统音频捕获 ==
[ok]   虚拟音源 wlk-sysaudio 存在
[ok]   默认输出设备可读
== 服务 ==
[ok]   识别服务 :8100
[ok]   字幕页 :8300
[ok]   桌面浮层 overlay.py
== 识别后端 ==
[ok]   显卡后端可用：whisper-turbo（whisper.cpp + HIP）
[ok]   ROCm 设备已识别
...
```

Files worth knowing:

| Path | Contents |
|---|---|
| `logs/launcher.log` | What `run.sh` started, model loads, startup failures |
| `logs/asr.log` | whisper-server or WhisperLiveKit output |
| `logs/ui.log` | `subtitler.py`: ASR connection, translation engine, websocket noise |
| `logs/overlay.log` | Overlay stderr; empty when all is well |
| `logs/screen.log` | Every caption and layout change the overlay drew |
| `~/.config/whisper-live/config.json` | Language pair, latency mode, cloud flag |
| `~/.config/whisper-live/overlay.json` | Mask, scale, dwell, position |

## No subtitles at all

Work from the audio end forwards.

1. **Is the virtual source there?**

   ```bash
   pw-dump | grep -n "wlk-sysaudio"
   wpctl status | grep -A3 "Sources"
   ```

   Missing means the loopback was not created, usually because there was no default sink when
   `run.sh` started. `run.sh` stops with:

   ```text
   找不到默认输出设备（wpctl inspect @DEFAULT_AUDIO_SINK@ 为空）
   ```

   Pick an output device in the desktop sound settings, then `./run.sh` again.

2. **Is the recognizer up?**

   ```bash
   curl -s http://127.0.0.1:8100/health; echo
   tail -n 40 logs/asr.log
   ```

   A running whisper-server logs its listen address near the start:

   ```text
   whisper server listening at http://127.0.0.1:8100
   ```

   If the file ends with a startup error instead, `run.sh` would have stopped with
   `识别服务没起来，看 logs/asr.log` or `模型缺失：.../model.bin （先跑 ./get-model.sh）`.

3. **Did the caption service connect to it?**

   ```bash
   grep -E "\[asr\]|\[mt\]|\[ui\]" logs/ui.log | tail -n 30
   ```

   Healthy output includes:

   ```text
   [asr] connected http://127.0.0.1:8100
   [mt] engine=local
   [mt] opus-mt ready (en -> zh, 0.9s)
   [ui] http://127.0.0.1:8300
   ```

   A refused connection shows the exception type, for example
   `[asr] ConnectionRefusedError: [Errno 111] Connection refused`, and the snapshot field
   `asr` becomes `offline`. The client retries every 3 s, so fixing the recognizer is enough.

4. **Is anything actually being decoded?**

   `logs/asr.log` gains a line per inference request, for example
   `operator(): processing a.wav (156160 samples, 9.8 sec)`. If that never appears, the capture
   is silent even though the service is healthy — check the audio meter on
   <http://127.0.0.1:8300> while something is playing.

   Repeated `Signal 0x... time stamps may be invalid.` lines are whisper.cpp noise and do not
   indicate a problem.

5. **Is the overlay just hidden?**

   ```bash
   tail -n 20 logs/screen.log
   ```

   `--- 超过 9 秒没有新字幕，浮层隐藏 ---` means the pipeline worked and then went quiet.
   `--- 浮层重新显示 ---` appears when a new caption arrives.

## Only English, no Chinese

The recognizer works, translation does not.

```bash
ls -lh models/opus-mt-en-zh/
grep -E "\[mt\]" logs/ui.log | tail -n 20
```

The directory needs at least `config.json`, `pytorch_model.bin`, `source.spm`,
`target.spm` and `vocab.json`. Fetch it with:

```bash
bash test/dl_opus.sh
```

Then restart. If loading still fails, `logs/ui.log` says why:

| Log line | Meaning |
|---|---|
| `[mt] opus load failed: ...` | Files present but unreadable; usually an interrupted download |
| `[mt] no translate-en_zh package under ...` | Argos fallback unavailable; install it or ignore if opus works |
| `[mt] 源语言是中文，只显示一行，不翻译` | Source and target are both Chinese, this is intended |
| `[mt] engine=argos` | Local opus was disabled; check that the package is installed |

If the language pair is not English to Chinese, the configuration window says so directly:
`这个方向没有本地翻译模型；勾选上面的云端润色才有译文，断网就没有。`

## Overlay does not appear

The browser page at <http://127.0.0.1:8300> is the control: if it shows captions, the pipeline
is fine and the problem is the window.

```bash
pgrep -af overlay.py
tail -n 30 logs/overlay.log
tail -n 5 logs/screen.log
```

- **No process.** Start only the overlay: `SNAP_URL=http://127.0.0.1:8300/snap
  .venv-qt/bin/python overlay.py`. A Qt error about the `xcb` platform plugin means the
  X11/XWayland libs are incomplete; on Ubuntu install `libxcb-cursor0`.
- **Process alive, nothing drawn.** `logs/screen.log` should contain a layout line such as
  `布局 1320x113 @ 200,640 mask=gray scale=1.0 screen=1536x960 dpr=2 geom=200,640`. No layout
  line means no caption arrived yet or `/snap` is unreachable.
- **`等待字幕服务…` on screen.** The overlay cannot reach the caption service. Check
  `curl -s http://127.0.0.1:8300/snap | head -c 200`, and confirm `SNAP_URL` matches the port.
- **Hidden after a pause.** The dwell timer did it. Set dwell to 0 in the configuration window,
  or just play something.
- **Under the video.** Some fullscreen players use a direct scanout or a layer shell that sits
  above XWayland. Switch the player to windowed or borderless mode.
- **Pure Wayland session.** There is no XWayland to host a global always-on-top window; use the
  browser page.

## ROCm is not used

`status.sh` reports one of:

```text
[ok]   显卡后端可用：whisper-turbo（whisper.cpp + HIP）
[warn] 显卡后端未装（跑 ./build-gpu.sh），将使用 CPU
[warn] whisper.cpp 没看见 ROCm 设备，可能只会在 CPU 上跑
```

Check what ROCm sees and what the binary was compiled for:

```bash
rocminfo | grep -m3 -E "Name:|gfx"
vendor/whisper.cpp/build/bin/whisper-server --help | grep -i "ROCm devices"
```

- No `gfx` line in `rocminfo`: the kernel driver or ROCm install is incomplete. This is
  outside the project; fix ROCm first.
- Device listed but the binary reports no ROCm devices: it was compiled for a different
  architecture. Rebuild with the value from `rocminfo`:

  ```bash
  AMDGPU_TARGETS=gfx1100 ./build-gpu.sh
  ```

  `build-gpu.sh` already defaults to the first `gfx` name it finds and to `gfx1151` when it
  finds none.
- `==> 启动识别服务 127.0.0.1:8100（whisper.cpp + 显卡，模型 ggml-model.bin）` in
  `logs/launcher.log` confirms the GPU path was chosen;
  `ggml_cuda_init: found 1 ROCm devices` in `logs/asr.log` confirms the runtime loaded it.
- To compare against CPU on purpose: `ASR_ENGINE=cpu ./run.sh`. Expect roughly 4.4 s to the
  first line with medium instead of 3.3 s with turbo, and a slower tail.

## Translation is slow, stuck, or repeats itself

- **Repeats a phrase.** Marian loops on short or noisy input. The code rejects output with
  repeated n-grams and falls back to Argos, but a bad line can still slip through. `MT=argos`
  avoids the Marian path entirely.
- **Cloud polish never arrives.** MyMemory has two attempts and then a 25 s cooldown; the log
  shows `[mt] mymemory ...` attempts. `PUBLISH_DEADLINE` publishes the local draft when the
  deadline passes, so captions keep moving. `MT=local` removes the network from the loop.
- **Lines appear late but in order.** That is the index gate: a line whose translation is not
  final holds back the ones behind it until `PUBLISH_DEADLINE`. If it happens constantly,
  choose the fast mode or a smaller source model.
- **Chinese text is traditional.** OpenCC converts to simplified; if that step fails the line is
  stored as produced. Not fatal.

## Model download fails

All downloaders are plain HTTP and print the file they were fetching.

`./get-model.sh`:

```text
下载 model.bin
失败: model.bin
```

The script exits non-zero on the first failed file and also treats a body containing
`"Success":false` as a failure. Delete the partial file under `models/<repo>/` and run it
again; interrupted files are not resumed.

`./build-gpu.sh`:

```text
!! 下载失败：model.safetensors
```

The whisper.cpp clone and the build directory are kept, so a re-run only repeats the download
and conversion. A missing `cmake` or `git` is reported before anything is downloaded.

`bash test/dl_opus.sh` prints an HTTP code and byte count per file. A `200` with a few hundred
bytes is an error page, not a model; check that `models/opus-mt-en-zh/pytorch_model.bin` is
around 300 MB.

ModelScope is used because Hugging Face was unreachable from the machine this was built on. If
ModelScope is unreachable for you, change the base URL in the three scripts; the file lists are
the only thing they depend on.

## No audio level, or the wrong device

```bash
wpctl status | head -n 30
wpctl inspect @DEFAULT_AUDIO_SINK@ | grep -E "node.name|node.description"
```

The loopback copies the default sink that existed at startup. After switching outputs (HDMI to
headphones, for example), restart:

```bash
./stop.sh && ./run.sh
```

`stop.sh` leaves the virtual source in place on purpose, so a restart is cheap. If the source
itself is stale, remove it in `pw-cli` or `pavucontrol` and run `run.sh` again.

## Ports are still in use after stop

`stop.sh` kills by pid file, then by command line, then waits for the ports. When a process
does not exit it prints:

```text
警告：端口 8100 仍被占用
```

Find and stop the owner:

```bash
ss -ltnp | grep -E "8100|8300"
pgrep -af "whisper-server|wlk serve|subtitler.py|overlay.py"
```

Do not start a second `run.sh` before the ports are free; the configuration window already
calls `stop.sh` and waits when it sees an occupied port.

## What to include in a bug report

```bash
./status.sh
git rev-parse --short HEAD
tail -n 60 logs/launcher.log
tail -n 60 logs/asr.log
grep -E "\[asr\]|\[mt\]|\[ui\]|Traceback" logs/ui.log | tail -n 60
```

Mention the GPU model and ROCm version (`rocminfo | head`), the source model size, and the
`LATENCY` / `MT` values in use. The caption text itself is not needed and may be private.
