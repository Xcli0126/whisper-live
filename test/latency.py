import asyncio, json, time, subprocess
from websockets.asyncio.client import connect

async def main():
    subprocess.Popen(["pw-play", "test/sample3.wav"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    first_buf = first_en = first_zh = None
    async with connect("ws://127.0.0.1:8300/ws") as ws:
        while time.time() - t0 < 34:
            try:
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except asyncio.TimeoutError:
                continue
            now = time.time() - t0
            if d.get("buffer") and first_buf is None:
                first_buf = now
            if any(l["en"] for l in d["lines"]) and first_en is None:
                first_en = now
            if any(l["zh"] for l in d["lines"]) and first_zh is None:
                first_zh = now
    fmt = lambda x: "n/a" if x is None else f"{x:.1f}s"
    print("buffer:", fmt(first_buf), "| first EN line:", fmt(first_en), "| first ZH:", fmt(first_zh))

asyncio.run(main())
