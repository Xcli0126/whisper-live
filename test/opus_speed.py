
import time, torch, transformers
from transformers import MarianMTModel, MarianTokenizer
transformers.logging.set_verbosity_error()
d="models/opus-mt-en-zh"
tok=MarianTokenizer.from_pretrained(d); mdl=MarianMTModel.from_pretrained(d).eval()
torch.set_num_threads(8)
kw={"max_length":64,"num_beams":2,"no_repeat_ngram_size":4}
tests=["Good morning everyone.","Welcome back to the show.","Today we are talking about the history of the city.",
"The Yule Harbor was rebuilt twice after the war.","Many of the warehouses are now apartments and offices.",
"And so, my fellow Americans.","Ask not what your country can do for you.","today we are testing a real-time subtitle system"]
for s in tests:
    t=time.monotonic()
    b=tok([s],return_tensors="pt",padding=True)
    with torch.no_grad(): o=mdl.generate(**b,**kw)
    print("%5.2fs  %-58s -> %s"%(time.monotonic()-t,s[:58],tok.batch_decode(o,skip_special_tokens=True)[0]))
