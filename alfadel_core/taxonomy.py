ALIASES={"hrfta7kik":"hrftwq3","rmqcard":"rqmcard","ismmorm":"ismorm","inmnsb":"ismnsb"}
CLITIC_CODES={"ART","DMT","WAW","FA","SIN","nida","dmm","lamz","lamibt","nountwk"}

def normalize_code(code:str)->str:
    code=(code or "").strip(); return ALIASES.get(code,code)

def main_component(composite_pos:str)->str:
    p=[x.strip() for x in (composite_pos or "").split("@") if x.strip() and x.strip() not in CLITIC_CODES]
    return normalize_code(p[-1]) if p else ""

def harmonized_tag(code:str)->str:
    code=normalize_code(code); lo=code.lower()
    if not code:return "OTHER"
    if lo.startswith("f3l"):return "VERB"
    if code=="ismAll" or lo in {"ismprs","ismfor","ismtrb","ismreg","ismcit","ismmth","ismlqb"}:return "PROPN"
    if lo.startswith("ismmas") or lo.startswith("ism4mas"):return "MASDAR"
    if lo=="ismdem":return "DEMONSTRATIVE"
    if lo=="ismwsl":return "RELATIVE"
    if lo.startswith("ism"):return "NOUN"
    if lo.startswith("adj") or lo.startswith("ada") or lo.startswith("act") or lo.startswith("pas"):return "ADJ"
    if lo.startswith("prn") or code=="dmm":return "PRONOUN"
    if lo in {"hrfgrr","hrfgrrcmplx"}:return "PREPOSITION"
    if lo.startswith("hrf") or code in {"MA","nida","lamz","lamibt"}:return "PARTICLE"
    if lo.startswith("zrf"):return "ADVERB"
    if lo.startswith("rqm") or lo.startswith("rmq"):return "NUM"
    return "OTHER"

def tag_from_composite_pos(pos:str)->str:
    return harmonized_tag(main_component(pos))
