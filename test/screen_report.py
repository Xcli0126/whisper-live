#!/usr/bin/env python3
"""读 logs/screen.log（浮层自己记的屏幕变化），输出「每个时刻屏幕上是哪两行」、「某一行停留了多久」，
并挑出停留时间过短、人眼读不完的那种闪动。

用法：.venv/bin/python test/screen_report.py [logs/screen.log] [--flash 0.7]
"""
import re
import sys
import unicodedata
from pathlib import Path

FLASH = 0.7                     # 短于这个秒数就算「看不清」
line_re = re.compile(r"^(\d\d:\d\d:\d\d) \[\s*([\d.]+)\] (zh|en)\s+上一版\s*([\d.]+)s\s+'?(.*?)'? -> '?(.*?)'?$")
mark_re = re.compile(r"^\d\d:\d\d:\d\d \[\s*([\d.]+)\] --- (.*) ---$")


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0] if args else Path(__file__).resolve().parent.parent / "logs" / "screen.log")
    events = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = mark_re.match(raw)
        if m:
            events.append({"t": float(m.group(1)), "kind": "mark", "text": m.group(2)})
            continue
        m = line_re.match(raw)
        if m:
            events.append({"t": float(m.group(2)), "kind": m.group(3), "shown": float(m.group(4)),
                           "old": m.group(5), "new": m.group(6)})
    if not events:
        print("日志是空的：", path)
        return 1

    print(f"日志：{path}")
    print(f"覆盖 {events[-1]['t']:.1f} 秒，共 {sum(1 for e in events if e['kind'] in ('zh', 'en'))} 次换行\n")

    for kind, title in (("zh", "中文行"), ("en", "源语言行")):
        ev = [e for e in events if e["kind"] == kind]
        if not ev:
            continue
        shown = sorted(e["shown"] for e in ev)
        flash = [e for e in ev if e["shown"] < FLASH]
        print(f"{title}: 换 {len(ev)} 次，停留 中位 {shown[len(shown)//2]:.2f}s / 最短 {shown[0]:.2f}s / "
              f"短于 {FLASH}s 的 {len(flash)} 次")
        if flash:
            print("  看不清的闪动：")
            for e in flash:
                print(f"    {e['t']:6.2f}s  只露 {e['shown']:.2f}s  {e['new']}")
        print()

    # 英文换到新的一句后，中文多久才跟上（跟读延迟）
    lags, pending_src, pending_t = [], None, None
    for e in events:
        if e["kind"] == "en":
            new_sentence = not e["new"].startswith(e["old"]) and not e["old"].startswith(e["new"])
            if new_sentence:
                pending_src, pending_t = e["new"], e["t"]
        elif e["kind"] == "zh" and pending_t is not None:
            lags.append((e["t"] - pending_t, pending_src, e["new"]))
            pending_t = None
    if lags:
        vals = sorted(x[0] for x in lags)
        print(f"中英文不同步的句子：{len(lags)} 句，中位 {vals[len(vals)//2]:.2f}s，最长 {vals[-1]:.2f}s")
        print("  （整句模式应该恒为 0：两行同一时刻换）")
        for lag, s, z in sorted(lags, reverse=True)[:3]:
            print(f"  差 {lag:.2f}s  {s[:40]!r} -> {z[:24]}")
        print()

    print("时间轴（整屏状态，只列变化点）：")
    state = {}
    last_t = None
    for e in events:
        if e["kind"] == "mark":
            print(f"  {e['t']:6.2f}s  ·· {e['text']}")
            continue
        state[e["kind"]] = e["new"]
        gap = "" if last_t is None else f"（距上一条 {e['t'] - last_t:.2f}s）"
        last_t = e["t"]
        zh, en = state.get("zh", ""), state.get("en", "")
        pad = " " * max(0, 28 - width(zh))
        print(f"  {e['t']:6.2f}s  {zh}{pad} | {en[:58]}  {gap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
