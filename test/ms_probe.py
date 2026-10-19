import json,urllib.request
cands = ["cstr/whisper-large-v3-turbo-GGUF","gpustack/whisper-large-v3-turbo-GGUF","keepitsimple/whisper-large-v3-turbo",
         "ggml-org/whisper.cpp","unsloth/whisper-large-v3-turbo","AI-ModelScope/whisper-large-v3",
         "keepitsimple/whisper-large-v3","cstr/whisper-large-v3-GGUF","gpustack/whisper-large-v3-GGUF",
         "iic/Whisper-large-v3","cstr/whisper-large-v3-turbo"]
for c in cands:
    url=f"https://www.modelscope.cn/api/v1/models/{c}/repo/files?Revision=master"
    try:
        d=json.load(urllib.request.urlopen(url, timeout=20))
        fs=d.get("Data",{}).get("Files",[])
        print(c, "->", ", ".join(f"{x['Name']}({x.get('Size',0)//1048576}MB)" for x in fs)[:220])
    except Exception as e:
        print(c, "-> ERR", type(e).__name__, getattr(e,'code',''))
