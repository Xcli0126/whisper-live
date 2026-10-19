
import time, sys, os
os.environ["HF_HUB_OFFLINE"]="1"
from transformers import MarianMTModel, MarianTokenizer
t0=time.time()
tok = MarianTokenizer.from_pretrained("models/opus-mt-en-zh")
mdl = MarianMTModel.from_pretrained("models/opus-mt-en-zh")
mdl.eval()
print("load %.2fs" % (time.time()-t0))
tests=["And so, my fellow Americans.","Ask not what your country can do for you.",
       "The old harbor was rebuilt twice after the war.","Today we are talking about the history of the city."]
import torch
for t in tests:
    t0=time.time()
    b=tok([t], return_tensors="pt", padding=True)
    with torch.no_grad():
        out=mdl.generate(**b, max_new_tokens=64, num_beams=1)
    zh=tok.batch_decode(out, skip_special_tokens=True)[0]
    print("%.2fs  %-52s -> %s" % (time.time()-t0, t, zh))
