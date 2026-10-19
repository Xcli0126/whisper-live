#!/usr/bin/env python3
"""System-audio -> English subtitles -> Chinese translation.

Pipeline:
  pw-record (PipeWire monitor / wlk-sysaudio)  ->  WhisperLiveKit /asr (WS, PCM)
      ->  each committed line            ->  Argos en->zh (thread pool)
      ->  browser (WS, port 8300)        ->  subtitles.html

Run:
  .venv/bin/python subtitler.py --node wlk-sysaudio --asr ws://127.0.0.1:8000/asr
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from asr_gpu import gpu_feed

try:
    from opencc import OpenCC
    _OPENCC = OpenCC("t2s")     # 繁体 -> 简体
except Exception:              # 没装就原样输出
    _OPENCC = None

HERE = Path(__file__).resolve().parent
HTML_PATH = HERE / "subtitles.html"

DEBOUNCE = float(os.environ.get("MT_DEBOUNCE", "0.4"))   # 等这么久再翻；纯本地可以压到 0

DBG = os.environ.get("MT_DEBUG") == "1"


def dbg(msg: str) -> None:
    if DBG:
        now = time.time()
        print(f"[{time.strftime('%H:%M:%S', time.localtime(now))}.{int(now * 1000) % 1000:03d}] {msg}", flush=True)


def _ts(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"0:{int(m):02d}:{s:05.2f}"


class Store:
    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.buffer = ""
        self.buffer_zh = ""
        self.buf_dirty: tuple[str, float] | None = None
        self.level = 0.0
        self.asr_connected = False
        self.status = "starting"
        self._dirty: dict[int, tuple[str, float]] = {}
        self.cap_src = ""       # 屏幕上正在显示的源语言行（上一句整句）
        self.cap_zh = ""        # 屏幕上正在显示的中文行（与 cap_src 同属一句）
        self._cap_idx = -1      # cap 对应的是第几句，只许往前走
        self._final: set = set()   # 已经定稿（不会再改）的句子序号
        self._committed_at: dict = {}   # 句子序号 -> 提交时刻，用来兜底放行
        self.same_lang = False  # 源语言就是中文：不翻译，只显示一行
        self.live_preview = False  # 快速模式：正在说的半句也上屏（字会边听边改）
        self._clients: set = set()
        self.version = 0
        self.seg_gen = 0      # 片段代次：旧片段的未定稿解码结果直接丢弃
        self.seg_committed = ""   # 当前片段里已经提交成字幕行的文本前缀
        self.seg_prev_text = ""   # 上一轮解码结果，用于两次一致的「稳定前缀」判断
        self.seg_done: set = set()  # 当前片段已经提交过的行（去重用）

    # ---------- ingest ----------
    def apply_full(self, msg: dict) -> bool:
        changed = False
        raw = msg.get("lines") or []
        new_lines: list[dict] = []
        # Indices must stay aligned with the ASR message: lines are addressed by
        # position, so silence placeholders keep their slot (empty en/zh) and the
        # list is never trimmed, otherwise every snapshot looks "changed" and the
        # translation debounce never fires.
        for i, ln in enumerate(raw):
            text = (ln.get("text") or "").strip()
            prev = self.lines[i] if i < len(self.lines) else None
            item = dict(prev or {"en": "", "zh": "", "start": "", "end": ""})
            item["start"] = ln.get("start") or item.get("start", "")
            item["end"] = ln.get("end") or item.get("end", "")
            if ln.get("speaker") == -2 or not text:
                if item["en"] or item["zh"]:
                    item["en"] = ""
                    item["zh"] = ""
                    self._dirty.pop(i, None)
                    changed = True
            elif text != item["en"]:
                self._dirty[i] = (text, time.monotonic())
                item["en"] = text
                item["zh"] = ""   # stale translation of the previous wording
                changed = True
            new_lines.append(item)
        if len(new_lines) != len(self.lines):
            changed = True
        for stale in [i for i in self._dirty if i >= len(new_lines)]:
            self._dirty.pop(stale, None)
        self.lines = new_lines
        buf = (msg.get("buffer_transcription") or "").strip()
        if buf != self.buffer:
            self.set_buffer(buf)
            changed = True
        if changed:
            self.version += 1
        return changed

    # ---------- 屏幕上的两行 ----------
    # cap_src / cap_zh 就是浮层正在显示的两行，成对更新：中文只属于它翻译的那句英文，
    # 别的句子的译文一律不许覆盖（否则会出现「英文已经是下一句，中文跳回上一句」）。
    @staticmethod
    def _alike(a: str, b: str) -> bool:
        a, b = re.sub(r"[^a-z0-9]", "", a.lower()), re.sub(r"[^a-z0-9]", "", b.lower())
        if not a or not b:
            return a == b
        return a.startswith(b) or b.startswith(a)

    def _publish(self, index: int) -> None:
        """把第 index 句（英文+中文都齐）推到屏幕上。

        只在「这句的译文到了」时换屏：屏幕上永远是上一句已经定型的整句，
        开始说下一句时它才整句出现。代价是慢一句，好处是英文行一句之内不动，
        人眼不用追着半个单词跑。序号只许往前走，旧句子的译文回来不会把屏幕拽回去。
        """
        if index < self._cap_idx or index >= len(self.lines):
            return
        ln = self.lines[index]
        zh = ln["zh"] or ln["en"]
        if zh is not ln["en"] and not zh.endswith(("。", "！", "？", "…", "，", ".", "!", "?")):
            zh += "。"          # 草稿常常没标点，上屏补齐，不然两行看着不齐
        self._cap_idx = index
        self.cap_src, self.cap_zh = ln["en"], zh
        dbg(f"publish #{index} {ln['en'][:40]!r} -> {ln['zh'][:24]!r}")

    def _flush(self) -> None:
        """按序号顺序把定稿的句子推上屏。

        译文在多个线程里并发跑，回来次序不定；这里拿序号当闸门，
        只有第 n 句定稿了才轮到 n+1，屏幕上不会出现「跳着换」。
        """
        nxt = self._cap_idx + 1
        while nxt in self._final:
            self._final.discard(nxt)
            self._publish(nxt)
            nxt += 1

    def commit_line(self, text: str, start: float, end: float) -> None:
        """GPU 后端用：一句说完直接提交成正式字幕行。"""
        idx = len(self.lines)
        draft = self.buffer_zh if self._alike(self.buffer, text) else ""
        self.lines.append({"en": text, "zh": draft, "start": _ts(start), "end": _ts(end)})
        self._dirty[idx] = (text, time.monotonic())
        self._committed_at[idx] = time.monotonic()
        dbg(f"commit #{idx} {text[:40]!r}")
        self.buffer = ""
        self.buffer_zh = ""
        self.buf_dirty = None
        if self.same_lang:
            self.lines[idx]["zh"] = text
            self._final.add(idx)
            self._flush()
        self.version += 1

    def set_zh(self, index: int, zh: str, final: bool = False) -> None:
        """final=True 表示这句的译文不会再改了，这时才把它推到屏幕上。

        一句中文会依次拿到「提交时带的草稿 → 本地完整草稿 → 云端润色」三版，
        只把最后一版上屏，屏幕上一句只换一次，不会草稿闪完润色再闪。
        """
        if not (0 <= index < len(self.lines)):
            return
        self.lines[index]["zh"] = zh or self.lines[index]["zh"]
        self.version += 1
        dbg(f"line{index} zh{'*' if final else ''} = {zh[:40]!r}")
        if final:
            self._final.add(index)
            self._flush()

    def set_buffer(self, text: str) -> None:
        text = (text or "").strip()
        if text == self.buffer:
            return
        self.buffer = text
        if not text:
            self.buffer_zh = ""       # 句子收尾，草稿作废；屏幕上的中文留在 cap_zh
        self.buf_dirty = (text, time.monotonic()) if text else None
        self.version += 1

    def set_buffer_zh(self, src: str, zh: str) -> None:
        if not zh:
            return
        if not (self._alike(src, self.buffer) or self._alike(src, self.cap_src)):
            dbg(f"drop draft {src[:40]!r}（屏幕上是 {self.cap_src[:40]!r}）")
            return                    # 不是屏幕上这句的译文，丢掉
        self.buffer_zh = zh
        self.version += 1

    # ---------- snapshot ----------
    def snapshot(self) -> dict:
        lines = [ln for ln in self.lines if ln["en"]]
        src, zh = self.cap_src, self.cap_zh
        # 快速模式：正在说的半句也上屏。但 buffer 若是上一句的残影（和屏幕上的话同源），
        # 就不接，否则收尾时会看到字幕「倒回去」一句。
        if self.live_preview and self.buffer and not self._alike(self.buffer, self.cap_src):
            src, zh = self.buffer, self.buffer_zh
        return {
            "lines": lines[-40:],
            "buffer": self.buffer,
            "buffer_zh": self.buffer_zh,
            "cap_src": src,
            "cap_zh": self.same_lang and src or zh,
            "same_lang": self.same_lang,
            "level": round(self.level, 3),
            "asr": self.asr_connected,
            "status": self.status,
            "v": self.version,
        }


async def audio_reader(node: str, q: asyncio.Queue, loop: asyncio.AbstractEventLoop, state: Store) -> None:
    cmd = [
        "pw-record", "--target", node, "--rate", "16000",
        "--channels", "1", "--format", "s16", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    CHUNK = 3200  # 100 ms @ 16 kHz s16 mono
    try:
        while True:
            data = await proc.stdout.read(CHUNK)
            if not data:
                state.status = "audio stream ended"
                break
            if len(data) >= 2:
                n = len(data) // 2
                acc = 0
                for i in range(0, len(data) - 1, 2):
                    s = int.from_bytes(data[i:i + 2], "little", signed=True)
                    acc += s * s
                rms = math.sqrt(acc / n) / 32768.0
                state.level = rms
            await q.put(data)
    finally:
        if proc.returncode is None:
            proc.kill()


async def feed(asr_url: str, q: asyncio.Queue, state: Store) -> None:
    sep = "&" if "?" in asr_url else "?"
    while True:
        try:
            async with ws_connect(f"{asr_url}{sep}mode=full", max_size=None, ping_interval=20) as ws:
                state.asr_connected = True
                state.status = "running"
                print(f"[asr] connected {asr_url}", flush=True)

                async def recv_loop() -> None:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(msg, dict) and msg.get("type") == "config":
                            continue
                        if isinstance(msg, dict) and msg.get("lines") is not None:
                            state.apply_full(msg)

                rtask = asyncio.create_task(recv_loop())
                try:
                    while True:
                        chunk = await q.get()
                        await ws.send(chunk)
                finally:
                    rtask.cancel()
        except Exception as exc:  # keep retrying; ASR may still be loading
            state.asr_connected = False
            state.status = f"asr offline: {type(exc).__name__}"
            print(f"[asr] {type(exc).__name__}: {exc}", flush=True)
        state.asr_connected = False
        while not q.empty():
            q.get_nowait()
        await asyncio.sleep(3)


ARGOS_DIR = Path(os.path.expanduser("~/.local/share/argos-translate/packages"))
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
CN_END = "。！？….!?"


def load_ct2(src: str, tgt: str):
    """Load an Argos .argosmodel's CTranslate2 engine directly.

    argostranslate's own translate() runs every input through a Stanza
    sentencizer whose packaged model is incomplete here, so Stanza tries to
    download on first use and hangs. The CTranslate2 model + sentencepiece
    files in the same package work fine on their own.
    """
    import ctranslate2
    import sentencepiece

    for pkg in sorted(ARGOS_DIR.glob(f"translate-{src}_{tgt}-*"), reverse=True):
        model_dir, spm = pkg / "model", pkg / "sentencepiece.model"
        if model_dir.is_dir() and spm.is_file():
            engine = ctranslate2.Translator(
                str(model_dir), device="cpu", compute_type="int8",
                inter_threads=2, intra_threads=0,
            )
            return engine, sentencepiece.SentencePieceProcessor(str(spm)), pkg.name
    return None, None, None


MM_LANG = {"zh": "zh-CN", "en": "en", "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "ru": "ru", "es": "es"}


def mymemory_translate(text: str, langs: tuple[str, str], timeout: float = 6.0) -> str:
    """Cloud MT over the public MyMemory API (no key, ~5k chars/day per IP).

    Noticeably more fluent than Argos for long sentences; the text of each
    subtitle line is sent to api.mymemory.translated.net.
    """
    import urllib.parse
    import urllib.request

    pair = f"{MM_LANG.get(langs[0], langs[0])}|{MM_LANG.get(langs[1], langs[1])}"
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": text[:480], "langpair": pair})
    req = urllib.request.Request(url, headers={"User-Agent": "realtime-subtitler/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    if "MYMEMORY WARNING" in out.upper():
        raise RuntimeError(out[:80])
    if not out or "\ufffd" in out:
        raise RuntimeError(f"bad result (status {data.get('responseStatus')}): {out[:60]}")
    return out


def argos_translate(engine, spm, text: str) -> str:
    out = []
    for chunk in SENT_SPLIT.split(text.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens = spm.encode(chunk, out_type=str)
        best = engine.translate_batch([tokens], beam_size=4, max_batch_size=8)[0]
        piece = spm.decode(best.hypotheses[0]).replace("▁", " ").strip()
        if piece.endswith("."):
            piece = piece[:-1] + "。"
        elif piece and piece[-1] not in CN_END:
            piece += {"!": "！", "?": "？"}.get(chunk[-1], "。")
        out.append(piece)
    return "".join(out)


def _repeats(text: str, n: int = 4) -> bool:
    """同一段 4 字以上重复出现，或者「历史的」这类 A 的 A 打转。"""
    if re.search(r"([\u4e00-\u9fff]{2,4})的\1", text):
        return True
    if re.search(r"([\u4e00-\u9fff]{1,3}[！？。]){3,}", text):   # 「不！不！不！」
        return True
    if len(text) < n * 2:
        return False
    seen = set()
    for i in range(len(text) - n + 1):
        piece = text[i:i + n]
        if piece in seen:
            return True
        seen.add(piece)
    return False


_CN_PUNCT = {",": "，", ".": "。", "?": "？", "!": "！", ";": "；", ":": "："}
_CJK = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"


def _tidy_zh(text: str) -> str:
    """本地模型会给半角标点和中文之间的空格，统一成中文习惯。"""
    if not text:
        return text
    out = []
    for i, ch in enumerate(text):
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch in _CN_PUNCT and (not nxt or nxt.isspace() or re.match(rf"[{_CJK}]", nxt)):
            out.append(_CN_PUNCT[ch])
        else:
            out.append(ch)
    return re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", "".join(out))


def _simp(text: str) -> str:
    """云端和本地模型都会偶尔吐繁体、半角标点，统一成简体中文的写法。"""
    text = _tidy_zh(text or "")
    if not text or _OPENCC is None:
        return text
    try:
        return _OPENCC.convert(text)
    except Exception:
        return text


OPUS_DIR = HERE / "models" / "opus-mt-en-zh"


class Mt:
    """两级翻译：本地 opus-mt 先出草稿（0.15 秒），云端再回填更好的版本。

    只靠云端的话，一句中文要等 1.5-3 秒才出现，字幕会明显落后于画面。
    """

    def __init__(self, langs: tuple[str, str], engine_kind: str = "auto") -> None:
        self.langs = langs
        self.want_cloud = engine_kind in ("auto", "mymemory")
        self.want_local = engine_kind in ("auto", "argos", "mymemory", "local")
        self.opus_tok = None
        self.opus_mdl = None
        self.opus_lock = threading.Lock()
        self.engine = self.spm = None
        self.cooldown = 0.0
        self.cloud_lock = threading.Lock()   # 并发打过去会互相拖慢到 3-5 秒

    # ---------- 装载 ----------
    def load(self) -> None:
        if not self.want_local:
            return
        if OPUS_DIR.exists() and self.langs == ("en", "zh"):
            try:
                import torch
                from transformers import MarianMTModel, MarianTokenizer
                import transformers
                transformers.logging.set_verbosity_error()
                t0 = time.monotonic()
                self.opus_tok = MarianTokenizer.from_pretrained(str(OPUS_DIR))
                self.opus_mdl = MarianMTModel.from_pretrained(str(OPUS_DIR)).eval()
                torch.set_num_threads(max(2, os.cpu_count() // 4))
                print(f"[mt] opus-mt ready ({self.langs[0]} -> {self.langs[1]}, {time.monotonic() - t0:.1f}s)", flush=True)
            except Exception as exc:
                print(f"[mt] opus load failed: {type(exc).__name__}: {exc}", flush=True)
                self.opus_tok = self.opus_mdl = None
        # opus 用不了、或某个半句被它翻成空的时候，Argos 兜底；两个都装上也就 0.05 秒
        try:
            engine, spm, name = load_ct2(*self.langs)
        except Exception as exc:
            print(f"[mt] argos load failed: {type(exc).__name__}: {exc}", flush=True)
            return
        if engine is None:
            print(f"[mt] no translate-{self.langs[0]}_{self.langs[1]} package under {ARGOS_DIR}", flush=True)
            return
        self.engine, self.spm = engine, spm
        print(f"[mt] {name} ready ({self.langs[0]} -> {self.langs[1]})", flush=True)

    # ---------- 本地草稿 ----------
    def fast(self, text: str) -> str:
        if self.opus_mdl is not None:
            import torch
            with self.opus_lock:
                batch = self.opus_tok([text], return_tensors="pt", padding=True)
                with torch.no_grad():
                    # 短句（"Good morning everyone."）会让 Marian 陷进「大家早,早,早,…」的循环，
                    # 一路生成到 max_length；禁掉 4-gram 重复 + 砍短上限，正常句子仍是 0.1 秒。
                    out = self.opus_mdl.generate(**batch, max_length=64, num_beams=2,
                                                 no_repeat_ngram_size=4)
                zh = _simp(self.opus_tok.batch_decode(out, skip_special_tokens=True)[0])
            # 太短的半句会让模型胡说（'所以..。说..。 And so'），带英文原样出来的直接丢
            if re.search(r"[A-Za-z]{2,}", zh) or len(zh.strip(" .。…")) < 2:
                return self._argos(text)
            if _repeats(zh.strip(" .。…")):   # 半句常被模型念成「历史的历史」
                return self._argos(text)
            # 两三个词的碎句（"Not!"）opus 会自己加戏成「不！不！不，不！」，字数超标就交给 Argos
            words = len(text.split())
            if words <= 2 and len(re.findall(rf"[{_CJK}]", zh)) > 3 * words + 1:
                return self._argos(text) or zh
            return zh
        return self._argos(text)

    def _argos(self, text: str) -> str:
        if self.engine is not None:
            try:
                zh = _simp(argos_translate(self.engine, self.spm, text))
                return "" if "\ufffd" in zh else zh     # int8 模型偶尔吐替换符，宁缺毋滥
            except Exception as exc:
                print(f"[mt] argos {type(exc).__name__}: {exc}", flush=True)
        return ""

    # ---------- 云端精修 ----------
    def cloud(self, text: str) -> str:
        if not self.want_cloud:
            return ""
        errs = []
        with self.cloud_lock:
            for attempt in range(2):
                if time.monotonic() < self.cooldown:
                    return ""
                try:
                    return _simp(mymemory_translate(text, self.langs))
                except Exception as exc:
                    errs.append(f"mymemory {type(exc).__name__}: {exc}")
                    time.sleep(0.5 * (attempt + 1))
            else:
                self.cooldown = time.monotonic() + 25   # 连续失败才短暂停用云端
        print(f"[mt] {' | '.join(errs)}", flush=True)
        return ""


PUBLISH_DEADLINE = float(os.environ.get("PUBLISH_DEADLINE", "2.4"))
# 句子提交后最多等这么久（秒），云端还没回来就先拿本地草稿上屏


async def translator(state: Store, mt: Mt) -> None:
    """正式字幕行：一句一换。

    草稿和润色都在后台线程里跑，谁先回来都行；上屏顺序由 Store 的序号闸门管，
    超过 PUBLISH_DEADLINE 还没润色完的句子先拿草稿上屏，不让整块屏幕排队等云端。
    """
    loop = asyncio.get_running_loop()
    jobs: dict[int, asyncio.Task] = {}

    async def one(i: int, text: str) -> None:
        draft = await loop.run_in_executor(None, mt.fast, text)
        dbg(f"line{i} fast {text[:50]!r} -> {draft!r}")
        if draft:
            state.set_zh(i, draft)          # 先存着，不上屏
        best = ""
        if mt.want_cloud:
            best = await loop.run_in_executor(None, mt.cloud, text)
            dbg(f"line{i} cloud {text[:50]!r} -> {best[:40]!r}")
        state.set_zh(i, best or draft, final=True)

    while True:
        await asyncio.sleep(0.15)
        now = time.monotonic()
        for i, (text, ts) in list(state._dirty.items()):
            if now - ts < DEBOUNCE:
                continue
            state._dirty.pop(i, None)
            if not text:
                state.set_zh(i, "", final=True)
            elif i not in jobs:
                task = asyncio.create_task(one(i, text))
                jobs[i] = task
                task.add_done_callback(lambda _t, k=i: jobs.pop(k, None))
        for i, t0 in list(state._committed_at.items()):
            if i in state._final or now - t0 < PUBLISH_DEADLINE:
                continue
            if i in jobs and not state.lines[i]["zh"]:
                continue        # 本地草稿还在跑，再等它一会儿，别把英文顶到中文行
            dbg(f"line{i} deadline 放行（云端未回）")
            state.set_zh(i, state.lines[i]["zh"], final=True)


BUFFER_DEBOUNCE = 0.25   # 「正在说的这句」停顿 0.25 秒就翻一次，本地草稿很快，不怕翻得多
BUFFER_MIN_CHARS = 6


async def buffer_translator(state: Store, mt: Mt) -> None:
    """正在说的这句也要中文，否则大字号那行会一直停在上上一句。"""
    loop = asyncio.get_running_loop()
    cache: dict[str, str] = {}
    while True:
        await asyncio.sleep(0.1)
        job = state.buf_dirty
        if not job:
            continue
        text, ts = job
        if time.monotonic() - ts < BUFFER_DEBOUNCE or len(text) < BUFFER_MIN_CHARS:
            continue
        state.buf_dirty = None
        # 半句最后一个词往往还没说完，翻出来是残词，先切掉再翻
        src = text if text[-1:] in ".!?" else text.rsplit(" ", 1)[0]
        if len(src) < BUFFER_MIN_CHARS:
            continue
        if src in cache:
            state.set_buffer_zh(text, cache[src])
            continue
        zh = await loop.run_in_executor(None, mt.fast, src)
        if zh:
            cache[src] = zh
            if len(cache) > 200:
                cache.clear()
        if zh:                      # 草稿不合格（重复/半截）时保留上一版，别把中文抹掉
            state.set_buffer_zh(text, zh)

async def broadcaster(state: Store) -> None:
    last = -1
    while True:
        await asyncio.sleep(0.2)
        if state.version == last:
            continue
        last = state.version
        payload = json.dumps(state.snapshot(), ensure_ascii=False)
        dead = []
        for ws in list(state._clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state._clients.discard(ws)


def make_process_request(html: bytes, state: Store):
    async def process_request(connection, request):
        path = request.path.split("?")[0]
        if path in ("/", "/index.html"):
            return Response(200, "OK", Headers([("Content-Type", "text/html; charset=utf-8")]), html)
        if path == "/healthz":
            return Response(200, "OK", Headers([("Content-Type", "text/plain")]), b"ok")
        if path == "/snap":
            body = json.dumps(state.snapshot(), ensure_ascii=False).encode()
            return Response(200, "OK", Headers([("Content-Type", "application/json; charset=utf-8")]), body)
        return None
    return process_request


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", default="wlk-sysaudio", help="PipeWire source node to record")
    ap.add_argument("--asr", default="ws://127.0.0.1:8000/asr",
                    help="ws://... WhisperLiveKit 或 http://... whisper-server")
    ap.add_argument("--asr-kind", default="wlk", choices=("wlk", "gpu"),
                    help="wlk = WhisperLiveKit(CPU)，gpu = whisper.cpp(显卡)")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--langs", default="en,zh", help="from,to language codes")
    ap.add_argument("--engine", default="auto", choices=("auto", "local", "argos", "mymemory"),
                    help="auto = MyMemory cloud first, Argos local as fallback")
    ap.add_argument("--live-preview", type=int, default=0,
                    help="1 = 快速模式：正在说的半句也上屏")
    args = ap.parse_args()

    html = HTML_PATH.read_bytes() if HTML_PATH.exists() else b"<h1>subtitles.html missing</h1>"
    state = Store()
    state.live_preview = bool(args.live_preview)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    loop = asyncio.get_running_loop()

    async def handler(ws) -> None:
        state._clients.add(ws)
        try:
            await ws.send(json.dumps(state.snapshot(), ensure_ascii=False))
            async for _ in ws:
                pass
        except Exception:
            pass
        finally:
            state._clients.discard(ws)

    langs = tuple(x.strip() for x in args.langs.split(","))
    state.same_lang = (langs[0] == langs[1]) or (
        langs[0].lower().split("-")[0] in ("zh", "cmn", "yue")
        and langs[1].lower().split("-")[0] in ("zh", "cmn", "yue")
    )
    mt = None
    if state.same_lang:
        print("[mt] 源语言是中文，只显示一行，不翻译", flush=True)
    else:
        mt = Mt((langs[0], langs[1]), args.engine)
        await asyncio.to_thread(mt.load)
        print(f"[mt] engine={args.engine}", flush=True)
    async with ws_serve(handler, "127.0.0.1", args.port, process_request=make_process_request(html, state), max_size=None):
        print(f"[ui] http://127.0.0.1:{args.port}", flush=True)
        asr_task = (gpu_feed(args.asr, q, state) if args.asr_kind == "gpu"
                    else feed(args.asr, q, state))
        tasks = [audio_reader(args.node, q, loop, state), asr_task, broadcaster(state)]
        if mt is not None:
            tasks.append(translator(state, mt))
            if state.live_preview:      # 只有「快速」档会显示半句，别的档别白算，白算还会拖慢整句
                tasks.append(buffer_translator(state, mt))
        await asyncio.gather(*tasks)


if __name__ == "__main__":  # noqa: C901
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
