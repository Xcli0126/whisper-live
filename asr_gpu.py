"""流式识别：whisper.cpp（ROCm/HIP 显卡）后端。

与 CPU 版共用同一条链路：pw-record 取 16 kHz 单声道 PCM → silero VAD 切句 →
每 0.8 秒把「当前这句话」整段送 whisper-server 解码，解出来的完整句子立刻提交成字幕行，
剩下的半句显示成未定稿绿字 → 静音 0.3 秒后整段再解一次，把尾巴补上。

显卡上单次解码约 0.4 秒（8 秒音频），所以刷新频率不构成负担。
"""
import asyncio
import io
import json
import os
import re
import time
import urllib.request
import uuid
import wave
from collections import deque

import numpy as np
import torch
from whisperlivekit.silero_vad_iterator import VADIterator, OnnxWrapper, load_onnx_session

DEBUG = os.environ.get("ASR_DEBUG") == "1"
SR = 16000
FRAME = 512                 # silero v6 固定 512 样本（32 ms）
MAX_SEG = 10.0              # 单段上限，超了强制断句，避免一直不提交
PARTIAL_EVERY = 0.45        # 未定稿刷新间隔（显卡够快，刷新越勤提交越早）
MIN_PARTIAL = 0.6           # 片段太短不刷未定稿
PRE_ROLL = 6                # 触发前的预留帧（约 200 ms）
BYTES_PER_SEC = SR * 2
MAX_NO_SPEECH = 0.45        # 超过就认为这段不是人声（whisper 自己的判决）
MIN_LOGPROB = -0.85         # 平均对数概率太低说明在硬凑
MIN_COMMIT_CHARS = 10       # 太短的一句先攒着，避免「Ask not!」这种碎行
SENT_END = re.compile(r"[.!?][\"')]?(?=\s|$)")


def _to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def _transcribe(server: str, pcm: bytes, timeout: float = 60.0) -> dict:
    """返回 {text, avg_logprob, no_speech_prob} —— 后两项用来丢幻觉。"""
    boundary = "----wlk" + uuid.uuid4().hex
    form = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="response_format"\r\n\r\n'
            "verbose_json\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="temperature"\r\n\r\n'
            "0.0\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n")
    body = form.encode() + _to_wav(pcm) + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        server.rstrip("/") + "/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if isinstance(data, list):
        data = data[0] if data else {}
    segs = data.get("segments") or []
    text = " ".join((data.get("text") or "").split())
    if not segs:
        return {"text": text, "avg_logprob": -9.0, "no_speech_prob": 1.0}
    worst_ns = max(s.get("no_speech_prob", 0.0) for s in segs)
    worst_lp = min(s.get("avg_logprob", 0.0) for s in segs)
    return {"text": text, "avg_logprob": worst_lp, "no_speech_prob": worst_ns}


def _split_complete(text: str) -> tuple[str, str]:
    """把已解出的文本切成「完整的句子」和「还没说完的尾巴」。"""
    last = None
    for m in SENT_END.finditer(text):
        last = m
    if last is None:
        return "", text
    return text[:last.end()].strip(), text[last.end():].strip()


def _norm(text: str) -> str:
    """比两次解码是否说的是同一句时用：去掉标点、空格和大小写差异。"""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _advance(text: str, count: int) -> int:
    """text 里第 count 个字母数字之后的位置，用来把「已上屏」映射回字符下标。"""
    seen = 0
    for i, ch in enumerate(text):
        if ch.isascii() and ch.isalnum():
            seen += 1
            if seen == count:
                return i + 1
    return len(text)


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


async def _decoder(server: str, jobs: "asyncio.Queue", state, loop) -> None:
    while True:
        kind, gen, start, end, pcm = await jobs.get()
        t_begin = time.monotonic()
        try:
            res = await loop.run_in_executor(None, _transcribe, server, pcm)
        except Exception as exc:            # 服务端超时/重启：别把整条链路带死
            state.status = f"asr error: {exc}"
            continue
        if gen != state.seg_gen and kind == "partial":
            continue                        # 已经进下一句了，旧的猜测作废
        text = res["text"]
        if DEBUG:
            print(f"[asr] {kind} gen={gen} len={end - start:.1f}s took={time.monotonic() - t_begin:.2f}s "
                  f"ns={res['no_speech_prob']:.2f} lp={res['avg_logprob']:.2f} "
                  f"text={text[:70]!r}", flush=True)
        if not text:
            if kind == "final":
                state.set_buffer("")
            continue
        # 静音/噪声上的幻觉：whisper 自己会给出「这段有没有人声」的概率
        if res["no_speech_prob"] > MAX_NO_SPEECH or res["avg_logprob"] < MIN_LOGPROB:
            if kind == "final":
                state.set_buffer("")
            continue
        # 已经上屏的部分按「去掉标点后等价」跟随解码文本。
        # whisper 会连着两次给同一句话配不同标点（\"city.\" / \"city,\"），
        # 按字符比前缀会误判成漂移、把整句拖到段落结束才上屏。
        if kind == "final":
            committed = _norm(state.seg_committed)
            if committed and not _norm(text).startswith(committed):
                state.seg_committed = ""        # 收尾这遍漂了：不补尾巴，已上屏的行留着
                state.seg_prev_text = ""
                state.set_buffer("")
                continue
            consumed = _advance(text, len(committed)) if committed else 0
            rest = text[consumed:].lstrip(" .,;:!?-—").strip()
            if len(rest) > 1 and _norm(rest) not in state.seg_done:
                state.commit_line(rest, start, end)
            state.seg_committed = ""
            state.seg_prev_text = ""
            state.seg_done = set()
            state.set_buffer("")
            continue
        prev = state.seg_prev_text
        state.seg_prev_text = text
        committed = _norm(state.seg_committed)
        if committed and not _norm(text).startswith(committed):
            state.seg_committed = ""            # 解码漂了，位置从头算（已上屏的行不动）
            committed = ""
        consumed = _advance(text, len(committed)) if committed else 0
        complete, _ = _split_complete(text)
        if DEBUG:
            print(f"[asr]   complete_len={len(complete)} consumed={consumed}", flush=True)
        if len(complete) > consumed:
            piece = complete[consumed:].lstrip(" .,;:!?-—")
            seen = _norm(piece)
            # 只信「这次解出的完整句，上一次解码里也说过」，并且本段没提交过
            if seen and len(piece) >= MIN_COMMIT_CHARS and seen in _norm(prev) \
                    and seen not in state.seg_done:
                # 时间戳按字符比例摊到片段内，够用且不需要词级对齐
                span = max(end - start, 0.01)
                total = max(len(text), 1)
                t0 = start + span * consumed / total
                t1 = start + span * len(complete) / total
                state.commit_line(piece, t0, t1)
                state.seg_done.add(seen)
                state.seg_committed = complete
        tail = text[len(state.seg_committed):].strip()
        state.set_buffer(tail)


async def gpu_feed(server: str, q: "asyncio.Queue", state) -> None:
    loop = asyncio.get_running_loop()
    model = OnnxWrapper(load_onnx_session(), force_onnx_cpu=True)
    vad = VADIterator(model, threshold=0.5, sampling_rate=SR,
                      min_silence_duration_ms=250, speech_pad_ms=120)
    jobs: asyncio.Queue = asyncio.Queue()
    decoder = asyncio.create_task(_decoder(server, jobs, state, loop))
    state.asr_connected = True
    state.status = "running"

    samples = 0                 # 全局样本计数，用来算时间戳
    seg = bytearray()
    seg_start = 0.0
    pre = deque(maxlen=PRE_ROLL)
    last_partial = 0.0
    leftover = b""

    def push_partial() -> None:
        nonlocal last_partial
        if len(seg) < MIN_PARTIAL * BYTES_PER_SEC:
            return
        if jobs.qsize() >= 1:         # 上一轮还没解完，别再往里塞，否则越积越慢
            last_partial = time.monotonic()
            return
        last_partial = time.monotonic()
        jobs.put_nowait(("partial", state.seg_gen, seg_start, samples / SR, bytes(seg)))

    def finalize() -> None:
        nonlocal seg
        if len(seg) >= 0.2 * BYTES_PER_SEC:
            state.seg_gen += 1
            jobs.put_nowait(("final", state.seg_gen, seg_start, samples / SR, bytes(seg)))
        seg = bytearray()

    try:
        while True:
            data = await q.get()
            if not data:
                state.status = "audio stream ended"
                break
            data = leftover + data
            cut = len(data) - (len(data) % (FRAME * 2))
            leftover = data[cut:]
            for off in range(0, cut, FRAME * 2):
                frame = data[off:off + FRAME * 2]
                samples += FRAME
                ev = vad(torch.from_numpy(
                    np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0))
                if ev and "start" in ev and not seg:
                    seg = bytearray(b"".join(pre))
                    seg_start = max(0.0, (samples - len(seg) // 2) / SR)
                    if DEBUG:
                        print(f"[asr] vad start t={samples / SR:.1f}s", flush=True)
                if seg:
                    seg += frame
                if ev and "end" in ev and seg:
                    if DEBUG:
                        print(f"[asr] vad end t={samples / SR:.1f}s seglen={(samples / SR - seg_start):.1f}s", flush=True)
                    finalize()
                elif seg and (samples - seg_start * SR) / SR >= MAX_SEG:
                    finalize()
                elif seg and time.monotonic() - last_partial >= PARTIAL_EVERY:
                    if DEBUG:
                        print(f"[asr] push partial seglen={(samples / SR - seg_start):.1f}s "
                              f"dt={time.monotonic() - last_partial:.2f}s", flush=True)
                    push_partial()
                if not seg:
                    pre.append(frame)
    finally:
        decoder.cancel()
