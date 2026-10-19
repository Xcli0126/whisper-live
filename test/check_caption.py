#!/usr/bin/env python3
"""边播音频边采样 /snap，检查两行字幕的稳定性（来源: 实测）。"""
import json
import subprocess
import sys
import time
import urllib.request

SNAP = "http://127.0.0.1:8300/snap"
wav = sys.argv[1]
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0

sink = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout
sink = [ln for ln in sink.splitlines() if "node.name" in ln][0].split('"')[1]
subprocess.Popen(["pw-play", "--target", sink, wav])
print("playing", wav, "->", sink)

rows, t0 = [], time.monotonic()
while time.monotonic() - t0 < dur:
    try:
        with urllib.request.urlopen(SNAP, timeout=1.0) as r:
            d = json.loads(r.read().decode())
    except Exception:
        time.sleep(0.3); continue
    rows.append((round(time.monotonic() - t0, 1), d["cap_src"], d["cap_zh"]))
    time.sleep(0.3)

blank_zh = 0
for (t, s, z), (pt, ps, pz) in zip(rows[1:], rows):
    if pz and not z:
        blank_zh += 1
        print(f"  !! {t}s 中文被清空（上一版 {pz!r}）")
blank_src = sum(1 for (t, s, z), (pt, ps, pz) in zip(rows[1:], rows) if ps and not s)
print(f"\n采样 {len(rows)} 次：中文被清空 {blank_zh} 次，源语言被清空 {blank_src} 次")
uniq = []
for t, s, z in rows:
    if not uniq or uniq[-1][1] != z:
        uniq.append((t, z, s))
for t, z, s in uniq:
    print(f"{t:5}s  {z or '—':<28} | {s[:60]}")
