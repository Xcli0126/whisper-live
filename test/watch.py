import asyncio, json
from websockets.asyncio.client import connect

def slim(d):
    return {
        "asr": d.get("asr"),
        "level": d.get("level"),
        "buffer": d.get("buffer", ""),
        "lines": [{"en": l.get("en", ""), "zh": l.get("zh", "")} for l in d.get("lines", [])],
    }

async def main():
    out = open("test/watch.jsonl", "w")
    async with connect("ws://127.0.0.1:8300/ws") as ws:
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
            except asyncio.TimeoutError:
                break
            d = json.loads(msg)
            out.write(json.dumps(slim(d), ensure_ascii=False) + "\n")
            out.flush()
    out.close()

asyncio.run(main())
