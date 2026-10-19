import json,urllib.request
def post(url, body):
    req=urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=25))
for name in ["ggml whisper", "whisper gguf", "whisper-large-v3-turbo gguf", "whisper.cpp"]:
    try:
        d=post("https://www.modelscope.cn/api/v1/dolphin/models", {"PageSize":8,"PageNumber":1,"SortBy":"Default","Target":"","SingleCriterion":[],"Name":name})
        models=d.get("Data",{}).get("Model",{}).get("Models",[]) or []
        print("##", name)
        for m in models[:8]:
            print("  ", m.get("Path") or m.get("Name"), m.get("Downloads"))
    except Exception as e:
        print("##", name, "ERR", e)
