
import json, time, urllib.request, urllib.parse, socket
Q="The old harbor was rebuilt twice after the war."
def get(url, data=None, timeout=8, headers=None):
    t0=time.time()
    req=urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                               headers=headers or {"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body=r.read(300).decode("utf-8","replace")
            return "OK %d %.2fs %s" % (r.status, time.time()-t0, body.replace("\n"," ")[:200])
    except Exception as e:
        return "ERR %.2fs %s" % (time.time()-t0, str(e)[:120])

tests = {
 "gtx": lambda: get("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q="+urllib.parse.quote(Q)),
 "libre_de": lambda: get("https://libretranslate.de/translate", {"q":Q,"source":"en","target":"zh"}),
 "libre_com": lambda: get("https://libretranslate.com/translate", {"q":Q,"source":"en","target":"zh"}),
 "terraprint": lambda: get("https://translate.terraprint.co/translate", {"q":Q,"source":"en","target":"zh"}, timeout=10),
 "argos_direct": None,
}
for k,f in tests.items():
    if f: print("%-12s %s" % (k, f()))
# DNS check
for host in ["translate.googleapis.com","libretranslate.de","www.modelscope.cn"]:
    try: print("dns %-30s %s" % (host, socket.gethostbyname(host)))
    except Exception as e: print("dns %-30s ERR %s" % (host, e))
