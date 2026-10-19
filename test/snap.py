import asyncio, json
from websockets.asyncio.client import connect

async def main():
    async with connect("ws://127.0.0.1:8300/ws") as ws:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print("lines:", len(d["lines"]))
        for l in d["lines"]:
            print("-", l["start"], l["end"], "|", l["en"][:60], "|", l["zh"][:40])

asyncio.run(main())
