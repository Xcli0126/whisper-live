
import asyncio, sys, time
sys.path.insert(0,'.')
import subtitler as S

async def main():
    st = S.Store()
    mt = S.Mt(("en","zh"), "auto")
    await asyncio.to_thread(mt.load)
    print("loaded", mt.engine is not None)
    st.commit_line("And so, my fellow Americans.", 0.0, 2.0)
    st.set_buffer("Ask what you can do")
    t0=time.time()
    t1 = asyncio.create_task(S.translator(st, mt))
    t2 = asyncio.create_task(S.buffer_translator(st, mt))
    for _ in range(30):
        await asyncio.sleep(0.5)
        print("%5.1f zh=%r buf_zh=%r" % (time.time()-t0, st.lines[-1]["zh"], st.buffer_zh))
        if st.lines[-1]["zh"] and st.buffer_zh: break
    t1.cancel(); t2.cancel()
asyncio.run(main())
