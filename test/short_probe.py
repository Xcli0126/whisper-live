
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subtitler as S
mt = S.Mt(("en", "zh"), "local"); mt.load()
for s in ["Not!", "Ask not!", "Thank you.", "Yes.", "Good morning everyone.", "No way."]:
    print(repr(s), "-> fast:", repr(mt.fast(s)))
