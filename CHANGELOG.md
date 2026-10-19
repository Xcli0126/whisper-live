# Changelog

## 0.1.0 — 2026-10-20

First public release.

- Capture the PipeWire default sink through a loopback source (`wlk-sysaudio`), so any playing
  application is subtitled without changing its output device.
- Streaming recognition with two backends: whisper.cpp + ROCm/HIP on an AMD GPU, or
  WhisperLiveKit + faster-whisper on the CPU. `run.sh` picks the GPU backend when it is built
  and the ggml model is present.
- GPU path with silero VAD sentence splitting, partial decodes every 0.45 s, and a two-decode
  agreement rule before a sentence is committed. Punctuation-insensitive prefix comparison
  keeps a finished sentence from waiting for the end of a paragraph.
- English → Chinese translation with opus-mt-en-zh (about 0.1–0.15 s per sentence), Argos
  CTranslate2 packages as fallback, optional MyMemory polish, OpenCC traditional-to-simplified
  conversion, and punctuation cleanup.
- Sentence-at-a-time caption publishing: the Chinese and English lines change together, in
  order, with a deadline that falls back to the local draft when the cloud is late.
- Desktop overlay (`overlay.py`, PySide6/XWayland): frameless, always on top, click-through,
  white text with black outline, optional gray or black rounded mask, configurable scale and
  dwell time. Position is set by dragging the overlay itself.
- Browser caption page on port 8300 with history, audio level and font-size controls.
- Configuration window (`app.py`) for languages, latency mode, cloud polish and overlay
  appearance. Settings persist under `~/.config/whisper-live/`.
- `build-gpu.sh` compiles whisper.cpp with HIP, downloads whisper-large-v3-turbo from
  ModelScope and converts it to ggml f16.
- `get-model.sh` downloads faster-whisper models from ModelScope for the CPU backend;
  `test/dl_opus.sh` fetches the opus-mt model.
- `install.sh` adds a desktop entry, icon and `~/.local/bin/whisper-live` launcher without
  sudo; `--uninstall` removes them.
- `status.sh` checks the virtual source, both services, the overlay, the GPU backend, the
  CPU models and the translation engines.
