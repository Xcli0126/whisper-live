
import json, urllib.request, urllib.parse
def probe(repo, fname="config.json"):
    url = "https://www.modelscope.cn/api/v1/models/%s/repo?Revision=master&FilePath=%s" % (repo, fname)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read(200)
            return "%s %s" % (r.status, body[:80].decode("utf-8","replace").replace("\n"," "))
    except Exception as e:
        return "ERR %s" % str(e)[:60]

cands = [
 "Helsinki-NLP/opus-mt-en-zh", "AI-ModelScope/opus-mt-en-zh", "keepitsimple/opus-mt-en-zh",
 "damo/nlp_csanmt_translation_en2zh", "iic/nlp_csanmt_translation_en2zh",
 "Helsinki-NLP/opus-mt-en-zh-multi", "gpustack/opus-mt-en-zh",
]
for c in cands:
    print("%-42s %s" % (c, probe(c)))
