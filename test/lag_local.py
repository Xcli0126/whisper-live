
import json,subprocess,time,urllib.request
p=subprocess.Popen(["pw-play","test/sample3.wav"],cwd=".",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
t0=time.monotonic(); last=None
while time.monotonic()-t0 < 13:
    try:
        d=json.load(urllib.request.urlopen("http://127.0.0.1:8300/snap",timeout=1))
    except Exception:
        time.sleep(0.1); continue
    cur=(d.get("cap_src") or "", d.get("cap_zh") or "")
    if cur!=last:
        print("%5.2f  %-46s | %s" % (time.monotonic()-t0, cur[0][:46], cur[1][:26]))
        last=cur
    time.sleep(0.1)
p.wait()
