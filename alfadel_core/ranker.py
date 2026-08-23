"""Native deterministic disambiguation for ALFADEL core Alpha 12.

This is a direct, dependency-free port of the *production* lexical and structural
logic frozen in Stage 13.  The experimental learned classifier remains disabled.
The ranker works on native :class:`Candidate` objects instead of serialized .an2
rows, allowing ALFADEL core to select analyses in memory.
"""
from __future__ import annotations
import collections,re
from pathlib import Path
import json
from .taxonomy import tag_from_composite_pos

CLITIC_POS={'ART','DMT','WAW','FA','SIN','MA','nida','dmm','lamz','lamibt','nountwk'}
AR_DIAC=re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
GRAMMATICAL_BASES={'ان','من','ما','اذا','اذ','لا','قد','ثم'}
DMT_SUFFIXES=['هما','كما','كم','كن','هم','هن','ها','نا','ني','ه','ك','ي']

def clean(x): return (x or '').strip()
def strip_diacritics(s): return AR_DIAC.sub('',clean(s)).replace('ـ','')
def normalize(s,aggressive=False):
    s=strip_diacritics(s).replace(' ','')
    if aggressive:
        s=s.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ٱ':'ا','ى':'ي'}))
    return s

def norm_segmented(s): return '@'.join(normalize(p,True) for p in clean(s).split('@'))
def concat_norm(s): return normalize(clean(s).replace('@',''),True)
def lexical_parts(s): return [normalize(p,True) for p in clean(s).split('@') if normalize(p,True)]
def base_surface(s):
    s=clean(s)
    if len(s)>2 and s[0] in 'وف' and normalize(s[1:],True) in GRAMMATICAL_BASES:
        return s[1:]
    return s

def semantic_core(pos):
    comps=[c for c in clean(pos).split('@') if c and c not in CLITIC_POS]
    while len(comps)>1 and comps[0]=='hrfgrr': comps=comps[1:]
    return comps[-1] if comps else ''

def clitic_signature(pos):
    comps=[c for c in clean(pos).split('@') if c]
    sig=[]
    for i,c in enumerate(comps):
        if c in CLITIC_POS: sig.append(c)
        elif c=='hrfgrr' and len(comps)>1 and i<len(comps)-1: sig.append('hrfgrr')
    return tuple(sig)

def same_lemma_root(a,b):
    return normalize(a.lemma,True)==normalize(b.lemma,True) and normalize(a.root,True)==normalize(b.root,True)

def compatibility(c,row):
    """Stage-13 corpus compatibility, adapted to native candidates."""
    gp=semantic_core(row.get('source_pos') or row.get('pos',''))
    cp=semantic_core(c.pos)
    score=0
    if gp and cp and gp==cp: score+=6
    elif gp and cp and (gp in cp or cp in gp): score+=3
    cl=normalize(c.lemma,True); gl=normalize(row.get('lemma',''),True)
    if cl and cl==gl: score+=6
    elif cl in lexical_parts(row.get('lemma','')): score+=6
    cr=normalize(c.root,True); gr=normalize(row.get('root',''),True)
    if cr and cr==gr: score+=4
    elif cr in lexical_parts(row.get('root','')): score+=4
    return score

def ranking_support(c,row):
    base=compatibility(c,row)
    if base<10: return 0
    weight={10:2,12:3,13:4,16:6}.get(base,max(1,base-8))
    candpos=clean(c.pos); goldpos=clean(row.get('source_pos') or row.get('pos',''))
    if candpos and candpos==goldpos:
        weight+=8
    else:
        gs=clitic_signature(goldpos); cs=clitic_signature(candpos)
        if gs and cs==gs: weight+=5
        elif gs and not cs: weight-=1
    if norm_segmented(c.lemma)==norm_segmented(row.get('lemma','')): weight+=4
    if norm_segmented(c.root)==norm_segmented(row.get('root','')): weight+=2
    return max(0,weight)

def particle_structure(surface):
    s=strip_diacritics(surface); expected=[]
    while len(s)>2 and s[0] in 'وف':
        expected.append('WAW' if s[0]=='و' else 'FA'); s=s[1:]
    if len(s)>2 and s[0] in 'لب':
        rem=normalize(s[1:],True)
        if any(rem==b or any(rem==b+normalize(suf,True) for suf in DMT_SUFFIXES) for b in GRAMMATICAL_BASES):
            expected.append('hrfgrr'); s=s[1:]
    collapsed=normalize(s,True); base=None; suffix=None
    for b in sorted(GRAMMATICAL_BASES,key=len,reverse=True):
        if collapsed==b: base=b; break
        for suf in DMT_SUFFIXES:
            if collapsed==b+normalize(suf,True): base=b; suffix=suf; break
        if base: break
    if suffix: expected.append('DMT')
    return base,suffix,tuple(expected)

def structure_score(surface,c):
    base,suffix,expected=particle_structure(surface)
    if not base: return 0
    sig=clitic_signature(c.pos); score=0
    for e in expected: score += 3 if e in sig else -3
    if not suffix and 'DMT' in sig: score-=2
    return score

def best_core_index(surface,cs,target,current):
    inds=[i for i,c in enumerate(cs) if semantic_core(c.pos)==target]
    if not inds: return current
    vals={i:structure_score(surface,cs[i]) for i in inds}; mx=max(vals.values())
    top=[i for i in inds if vals[i]==mx]
    return current if current in top else top[0]

def _contains_lam_shadda(surface): return 'لّ' in clean(surface)
def _letters_only(surface): return strip_diacritics(surface)
def _find_category_index(cs,exact_pos=None,core=None,require_clitics=()):
    inds=[]
    for i,c in enumerate(cs):
        if exact_pos is not None and clean(c.pos)!=exact_pos: continue
        if core is not None and semantic_core(c.pos)!=core: continue
        sig=clitic_signature(c.pos)
        if any(x not in sig for x in require_clitics): continue
        inds.append(i)
    return inds

class NativeRanker:
    def __init__(self,training_path:Path):
        self.training_path=Path(training_path)
        self.exact=collections.defaultdict(list)
        self.support_cache={}
        self._load()

    def _load(self):
        if not self.training_path.exists() or self.training_path.stat().st_size == 0:
            return
        with self.training_path.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                r=json.loads(line); work=clean(r.get('work'))
                if work in {'Abou_Qurra','GRNA_Or_43_ARA','GRNA Or. 43','GRNA'}: continue
                self.exact[clean(r.get('normalized_surface') or r.get('surface'))].append(r)

    def lexical_rank(self,surface,cs):
        if not cs: return None,[], 'no_candidate'
        skey=clean(surface); rows=self.exact.get(skey,[]); supports=[]
        for c in cs:
            ck=(skey,c.key)
            if ck not in self.support_cache:
                self.support_cache[ck]=float(sum(ranking_support(c,r) for r in rows))
            support=self.support_cache[ck]
            # A personally confirmed lexicon entry is explicit scholarly evidence,
            # not a learned guess.  It should outrank generic corpus priors while
            # remaining subject to the same visible structural guards.  Multiple
            # user entries for one surface receive the same boost and can still be
            # distinguished by corpus/structural evidence or manual selection.
            sources={e.source for e in getattr(c,'evidence',[])}
            if 'user_lexicon_exact' in sources: support += 500.0
            elif 'user_lexicon_historical' in sources: support += 250.0
            elif any(x.startswith('user_lexicon_') for x in sources): support += 200.0
            supports.append(support)
        if max(supports,default=0)>0:
            rank=max(range(len(cs)), key=lambda j:(supports[j],c_score(cs[j]),-j))
            return rank,supports,'lexical_corpus_prior'
        # Native equivalent of the Stage-13 candidate-audit fallback: keep the
        # strongest native evidence score when there is no exact-surface corpus prior.
        rank=max(range(len(cs)),key=lambda j:(c_score(cs[j]),-j))
        return rank,supports,'native_evidence_prior'

    def rank(self,surfaces,candidate_lists):
        ranks=[]; supports=[]; reasons=[]
        for s,cs in zip(surfaces,candidate_lists):
            r,ss,why=self.lexical_rank(s,cs); ranks.append(r); supports.append(ss); reasons.append(why)
        protected=[False]*len(ranks)
        # Stage-13 grammatical safeguards use next-token *lexical* choice.
        for i,(surface,cs) in enumerate(zip(surfaces,candidate_lists)):
            if not cs or ranks[i] is None: continue
            r=ranks[i]
            if r!=0 and cs[0].pos=='ismorm' and cs[r].pos=='adj2act' and same_lemma_root(cs[0],cs[r]):
                ranks[i]=0; reasons[i]='guard_agent_noun_vs_active_participle'; protected[i]=True; r=0
            nexttag=''
            if i+1<len(candidate_lists) and candidate_lists[i+1] and ranks[i+1] is not None:
                nexttag=tag_from_composite_pos(candidate_lists[i+1][ranks[i+1]].pos)
            base,suffix,expected=particle_structure(surface)
            cores={semantic_core(c.pos) for c in cs}
            if base=='ان' and suffix and 'hrfinna' in cores:
                ranks[i]=best_core_index(surface,cs,'hrfinna',ranks[i]); reasons[i]='guard_inna_anna_attached_pronoun'; protected[i]=True; continue
            if base=='ان' and 'hrfnsb' in cores and 'hrfinna' in cores:
                target='hrfnsb' if nexttag=='VERB' else ('hrfinna' if nexttag in {'NOUN','ADJ','PROPN','PRONOUN'} else None)
                if target:
                    ranks[i]=best_core_index(surface,cs,target,ranks[i]); reasons[i]='guard_an_core_'+target; protected[i]=True; continue
            if base=='ان' and 'hrfsrt' in cores and 'hrfinna' in cores:
                target='hrfsrt' if nexttag=='VERB' else ('hrfinna' if nexttag in {'NOUN','ADJ','PROPN','PRONOUN'} else None)
                if target:
                    ranks[i]=best_core_index(surface,cs,target,ranks[i]); reasons[i]='guard_in_core_'+target; protected[i]=True; continue

        # Orthographic/segmentation guards from Stage 10/13.
        for i,(surface,cs) in enumerate(zip(surfaces,candidate_lists)):
            if not cs or ranks[i] is None: continue
            letters=_letters_only(surface)
            core_letters=letters; outer=[]
            while len(core_letters)>2 and core_letters[0] in 'وف':
                outer.append('WAW' if core_letters[0]=='و' else 'FA'); core_letters=core_letters[1:]
            if _contains_lam_shadda(surface) and normalize(core_letters,True)=='الا':
                exact=(('WAW@' if 'WAW' in outer else '')+'hrfnsb@hrfnaf') if outer else None
                inds=_find_category_index(cs,exact_pos=exact) if exact else []
                if not inds:
                    inds=[j for j,c in enumerate(cs) if 'hrfnsb' in clean(c.pos).split('@') and ('hrfnaf' in clean(c.pos).split('@') or semantic_core(c.pos)=='hrfnsb')]
                    if outer: inds=[j for j in inds if all(x in clitic_signature(cs[j].pos) for x in outer)] or inds
                if inds:
                    ranks[i]=max(inds,key=lambda j:(clean(cs[j].pos).count('@'),structure_score(surface,cs[j]),-j)); reasons[i]='guard_visible_alla_contraction'; protected[i]=True; continue
            if normalize(letters,True)=='لم' and surface.rstrip().endswith('\u064e'):
                inds=_find_category_index(cs,core='ismstfh')
                if inds:
                    ranks[i]=inds[0]; reasons[i]='guard_vocalized_lima_interrogative'; protected[i]=True; continue
            expected=[]; s0=letters
            if len(s0)>2 and s0[0]=='و': expected.append('WAW')
            elif len(s0)>2 and s0[0]=='ف': expected.append('FA')
            rest=s0[1:] if expected else s0
            if rest.startswith('ال') and not any(normalize(rest,True).startswith(normalize(x,True)) for x in ('الذي','التي','الذين','اللاتي','اللائي')):
                expected.append('ART')
            if expected:
                cur=cs[ranks[i]]; csig=clitic_signature(cur.pos)
                if any(e not in csig for e in expected):
                    ccore=semantic_core(cur.pos); clem=concat_norm(cur.lemma); croot=concat_norm(cur.root); alts=[]
                    for j,c in enumerate(cs):
                        sig=clitic_signature(c.pos)
                        if not all(e in sig for e in expected): continue
                        if semantic_core(c.pos)!=ccore: continue
                        if (clem and concat_norm(c.lemma)==clem) or (croot and concat_norm(c.root)==croot): alts.append(j)
                    if alts:
                        ranks[i]=max(alts,key=lambda j:(structure_score(surface,cs[j]),-j)); reasons[i]='guard_visible_outer_clitics'; protected[i]=True
        return ranks,supports,reasons,protected

def c_score(c):
    try:return float(c.score)
    except:return 0.0
