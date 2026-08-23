from __future__ import annotations
import re
# Arabic words including Arabic supplement/extended letters; punctuation is not analyzed as a token.
AR_WORD=re.compile(r"[\u0621-\u063A\u0641-\u064A\u066E-\u06D3\u06FA-\u06FF]+(?:[\u064B-\u065F\u0670]*)")
def tokenize(text:str)->list[str]:
    return AR_WORD.findall(text or "")
