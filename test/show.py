import json
d = json.loads(open("test/watch.jsonl").readlines()[-1])
for l in d["lines"]:
    print("EN:", l["en"])
    print("ZH:", l["zh"])
