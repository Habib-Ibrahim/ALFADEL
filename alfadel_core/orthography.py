"""Historical Arabic orthography support.

The conservative key deliberately mirrors the rules validated in legacy validation stage.
Rules generate/validate candidate matches; they never overwrite the manuscript surface.
"""
from __future__ import annotations
import re, unicodedata

AR_DIAC=re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
HAMZA_ALIF=set('اأإآٱ')

RULE_LABELS={
    "TA_MARBUTA_AS_HA":"tāʾ marbūṭa written as hāʾ (ة → ه)",
    "HAMZA_ON_YA_AS_YA":"hamza-on-yāʾ written as yāʾ (ئ → ي)",
    "HAMZA_ON_WAW_AS_WAW":"hamza-on-wāw written as wāw (ؤ → و)",
    "INITIAL_HAMZA_OMITTED":"initial hamza seat omitted/neutralized (أ/إ/آ → ا)",
    "ALIF_MAQSURA_AS_YA":"alif maqṣūra written as yāʾ (ى → ي)",
    "FINAL_HAMZA_OMITTED":"final hamza after alif omitted (اء → ا)",
    "ONE_ALIF_VARIANT":"one non-initial alif inserted/omitted",
}

def strip_diacritics(s:str)->str:
    s=unicodedata.normalize("NFC",s or "")
    return AR_DIAC.sub('',s).replace('ـ','').replace(' ','')

def safe_key(s:str)->str:
    x=strip_diacritics(s)
    if x and x[0] in HAMZA_ALIF: x='ا'+x[1:]
    if x.endswith('ى'): x=x[:-1]+'ي'
    return x

def historical_key(s:str)->str:
    x=safe_key(s).translate(str.maketrans({'ئ':'ي','ؤ':'و'}))
    if x.endswith('ة'): x=x[:-1]+'ه'
    if x.endswith('اء'): x=x[:-1]
    return x

def fully_collapsed_key(s:str)->str:
    return strip_diacritics(s).translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ٱ':'ا','ى':'ي','ئ':'ي','ؤ':'و'}))

def delete_one_internal_alif_keys(s:str)->set[str]:
    x=historical_key(s)
    return {x[:i]+x[i+1:] for i,ch in enumerate(x) if ch=='ا' and 0<i<len(x)-1}


def single_redundant_letter_variants(s:str)->set[str]:
    """Delete one likely accidental duplicated written letter.

    Only adjacent identical letters, or two adjacent members of the alif/hamza
    family, are considered.  Callers must still require the resulting form to
    have an existing lexical/morphological analysis.  This keeps the rule a
    conservative rescue rather than generic fuzzy spelling.
    """
    x=strip_diacritics(s); out=set()
    for i in range(1,len(x)):
        if x[i]==x[i-1] or (x[i] in HAMZA_ALIF and x[i-1] in HAMZA_ALIF):
            out.add(x[:i]+x[i+1:])
            out.add(x[:i-1]+x[i:])
    return {v for v in out if v and v!=x}

def is_one_alif_variant(a:str,b:str)->bool:
    a=historical_key(a); b=historical_key(b)
    if abs(len(a)-len(b))!=1:return False
    short,long=(a,b) if len(a)<len(b) else (b,a)
    return short in delete_one_internal_alif_keys(long)

def explain_match(surface:str, canonical:str)->list[str]:
    """Explain attested graphic differences when the historical keys agree."""
    s=strip_diacritics(surface); c=strip_diacritics(canonical); rules=[]
    if c and s and c[0] in {'أ','إ','آ','ٱ'} and s[0]=='ا': rules.append("INITIAL_HAMZA_OMITTED")
    if c.endswith('ى') and s.endswith('ي'): rules.append("ALIF_MAQSURA_AS_YA")
    if c.endswith('ة') and s.endswith('ه'): rules.append("TA_MARBUTA_AS_HA")
    if c.endswith('اء') and s.endswith('ا'): rules.append("FINAL_HAMZA_OMITTED")
    # Same-position graphic substitutions where possible.
    for a,b in zip(c,s):
        if a=='ئ' and b=='ي' and "HAMZA_ON_YA_AS_YA" not in rules: rules.append("HAMZA_ON_YA_AS_YA")
        if a=='ؤ' and b=='و' and "HAMZA_ON_WAW_AS_WAW" not in rules: rules.append("HAMZA_ON_WAW_AS_WAW")
    if is_one_alif_variant(s,c): rules.append("ONE_ALIF_VARIANT")
    return rules

def label(rule:str)->str:return RULE_LABELS.get(rule,rule)
