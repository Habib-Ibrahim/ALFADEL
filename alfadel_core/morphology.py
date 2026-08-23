"""Native Alpha candidate generation.

This ports the safe generalized morphology used in Stage 12. It is intentionally
not yet claimed to reproduce every legacy paradigm; compatibility mode remains
available until parity tests are complete.
"""
from __future__ import annotations
import collections
from .lexicon import skeleton, norm, LexiconEntry
from .orthography import historical_key, explain_match, label, is_one_alif_variant, delete_one_internal_alif_keys, single_redundant_letter_variants
from .taxonomy import tag_from_composite_pos
from .models import Candidate, Evidence

PREFIX_MAP={
 'ويا':('و@يَا@','WAW@hrfcll@','و@يا@'),'فيا':('ف@يَا@','FA@hrfcll@','ف@يا@'),
 'وال':('و@ال@','WAW@ART@','و@ال@'),'فال':('ف@ال@','FA@ART@','ف@ال@'),
 'بال':('ب@ال@','hrfgrr@ART@','ب@ال@'),'كال':('ك@ال@','hrfgrr@ART@','ك@ال@'),
 'لل':('ل@ال@','hrfgrr@ART@','ل@ال@'),'يا':('يَا@','hrfcll@','يا@'),
 'ال':('ال@','ART@','ال@'),'و':('و@','WAW@','و@'),'ف':('ف@','FA@','ف@'),
 'ب':('ب@','hrfgrr@','ب@'),'ك':('ك@','hrfgrr@','ك@'),'ل':('ل@','hrfgrr@','ل@')}
PREFIXES=sorted(PREFIX_MAP,key=len,reverse=True)

ARTICLE_PREFIXES={'ال','وال','فال','بال','كال','لل'}

def chain_has_article(chain):
    return any(p in ARTICLE_PREFIXES for p in chain)

def article_compatible_candidate(chain,c):
    # Arabic definite article attaches to nominal categories, not finite verbs.
    return not chain_has_article(chain) or c.broad_pos != 'VERB'

DMT_SUFFIXES=['هما','كما','كن','كم','هن','هم','ها','نا','ني','ه','ك','ي']
PERF_SUFFIXES=['','ت','تا','تما','تم','تن','نا','وا','و','ن']
IMP_PREFIXES=['ي','ت','ا','ن']; IMP_SUFFIXES=['','ان','ون','ين','ن','ا','وا']

def collapse_gem(s):
    out=[]
    for ch in s:
        if out and out[-1]==ch:continue
        out.append(ch)
    return ''.join(out)

def nominal_forms(e:LexiconEntry):
    """Generate conservative written nominal/adjectival inflections.

    Alpha 2 ports recurrent branches of the legacy noun/adjective analyzer:
    feminine agreement, sound plural/dual endings, and written accusative alif.
    These rules only add candidates; the lexicon entry remains the linguistic
    identity and provenance of every analysis.
    """
    forms=set(); pos=e.pos
    for f in (e.lemma,e.secondary):
        if not f or f=='-':continue
        x=skeleton(f)
        if not x:continue
        forms.add(x)
        if pos.startswith(('hrf','prn','zrf','rqm')):
            continue

        # Indefinite accusative spelling, e.g. ثواب -> ثوابا.
        if len(x)>=3 and not x.endswith(('ا','ى','ي','ة','ات','ون','ين')):
            forms.add(x+'ا')

        # Feminine nouns/adjectives and sound feminine inflection.
        if x.endswith('ة') and len(x)>=3:
            stem=x[:-1]
            forms.update({stem+'ات',stem+'تان',stem+'تين',stem+'ت'})
        elif pos.startswith('adj'):
            # Legacy adjective loops reconstruct feminine agreement. Defective
            # active participles lose final yāʾ in the indefinite masculine
            # written form (فانٍ، باقٍ، قاضٍ، داعٍ) but restore it before the
            # feminine ending: فانية، باقية، قاضية، داعية.  Detect this from
            # the lexicon's final tanwīn-kasr rather than from a word list.
            if (e.lemma or '').strip().endswith('ٍ'):
                forms.add(x+'ية')
            else:
                # Other defective forms such as مُسْتَوٍ are represented with
                # final wāw in the unvocalized skeleton and likewise form -وية.
                forms.add(x+('ية' if x.endswith('و') else 'ة'))
            forms.update({x+'ون',x+'ين'})

        if len(x)>=3:
            forms.update({x+'ان',x+'ين'})
    return forms

def verb_forms(e:LexiconEntry):
    p=skeleton(e.lemma); i=skeleton(e.secondary if e.secondary!='-' else ''); r=skeleton(e.root); forms=set()
    pstems={p}
    if p.endswith(('ا','ى','ي')) and r:pstems.add(p[:-1]+r[-1])
    for st in pstems:
        for suf in PERF_SUFFIXES:forms.add(st+suf)
    if i:
        st=i[1:] if i[0] in 'يتان' else i; stems={st,collapse_gem(st)}
        if st.endswith(('ي','ى','و','ا')):stems.add(st[:-1])
        for stem in stems:
            for pr in IMP_PREFIXES:
                for suf in IMP_SUFFIXES:forms.add(pr+stem+suf)
            for pr in ('','ا'):
                for suf in ('','ي','ا','وا','و','ن'):forms.add(pr+stem+suf)
    forms|={collapse_gem(x) for x in list(forms)}
    return {x for x in forms if len(x)>=2}

class MorphologyIndex:
    def __init__(self, lexicon):
        self.lexicon=lexicon
        self.inflect=collections.defaultdict(list); self.verb=collections.defaultdict(list)
        self.historical_inflect=collections.defaultdict(list)
        self._build()
    def _build(self):
        for e in self.lexicon.entries:
            kind=self.lexicon.kind.get(e.file,'nominal')
            forms=verb_forms(e) if kind=='verb' else nominal_forms(e)
            target=self.verb if kind=='verb' else self.inflect
            for form in forms:
                target[norm(form)].append((e,form))
                self.historical_inflect[historical_key(form)].append((e,form,kind))
    def base_candidates(self,surface:str,historical=True):
        out=[]; n=norm(surface)
        for e,form in self.lexicon.exact.get(n,[]): out.append(self._cand(e,'exact_lexicon',10.5,form,surface,False))
        for e,form in self.inflect.get(n,[]): out.append(self._cand(e,'nominal_morphology',5.5,form,surface,False))
        for e,form in self.verb.get(n,[]): out.append(self._cand(e,'verb_morphology',5.5,form,surface,False))
        if historical and not out:
            # Conservative scribal/typing duplication rescue: only delete one
            # adjacent duplicate when the resulting written form is already a
            # valid native lexical or inflectional candidate.
            for corrected in single_redundant_letter_variants(surface):
                for e,form in self.lexicon.exact.get(norm(corrected),[]):
                    c=self._cand(e,'historical_redundant_letter',7.2,form,surface,False);c.historical_rules.append(f'accidental duplicated letter: {surface} → {corrected}');out.append(c)
                for e,form in self.inflect.get(norm(corrected),[]):
                    c=self._cand(e,'historical_redundant_letter',6.4,form,surface,False);c.historical_rules.append(f'accidental duplicated letter: {surface} → {corrected}');out.append(c)
                for e,form in self.verb.get(norm(corrected),[]):
                    c=self._cand(e,'historical_redundant_letter',6.4,form,surface,False);c.historical_rules.append(f'accidental duplicated letter: {surface} → {corrected}');out.append(c)
        if historical and n.endswith('و'):
            # Historical/manuscript omission of alif al-fariqa after plural waw,
            # e.g. يقولو -> يقولوا.  We only license this when the +ا form is
            # already generated by the native verb paradigm.
            for e,form in self.verb.get(n+'ا',[]):
                c=self._cand(e,'historical_final_alif_after_waw_omitted',6.9,form,surface,False)
                c.historical_rules.append('final differentiating alif after wāw omitted (وا → و)')
                out.append(c)
        if historical:
            hk=historical_key(surface)
            for e,form in self.lexicon.historical.get(hk,[]):
                if norm(form)!=n:out.append(self._cand(e,'historical_lexicon',9.0,form,surface,True))
            for e,form,kind in self.historical_inflect.get(hk,[]):
                if norm(form)!=n:out.append(self._cand(e,'historical_'+kind+'_morphology',7.0,form,surface,True))
            # Conservative one-alif recovery validated in the frozen Stage-13 layer.
            # Search both directions: manuscript has an extra internal alif, or the
            # lexical form has an alif omitted by the manuscript spelling.
            for short_key in delete_one_internal_alif_keys(surface):
                for e,form in self.lexicon.historical.get(short_key,[]):
                    if is_one_alif_variant(surface,form):
                        out.append(self._cand(e,'historical_one_alif_variant',6.8,form,surface,True))
            for e,form in self.lexicon.one_alif_deleted.get(hk,[]):
                if is_one_alif_variant(surface,form):
                    out.append(self._cand(e,'historical_one_alif_variant',6.8,form,surface,True))
        return self._dedup(out)
    def _prefix_paths(self, surface:str, max_depth:int=3):
        """Yield (prefix_chain, stem) decompositions without crossing tiny stems.

        Prefixes are returned in surface order.  Multi-prefix forms such as
        ``وللخلق`` can therefore be represented as ``و + لل + خلق`` and
        ``فللابن`` as ``ف + لل + ابن``.  This mirrors the legacy analyzer's
        iterative prefix stripping while keeping the process bounded.
        """
        seen=set()
        def rec(rest, chain, depth):
            if chain:
                k=(tuple(chain),rest)
                if k not in seen:
                    seen.add(k); yield chain,rest
            if depth>=max_depth:return
            for pref in PREFIXES:
                if rest.startswith(pref) and len(rest)>len(pref)+1:
                    yield from rec(rest[len(pref):],chain+[pref],depth+1)
        yield from rec(surface,[],0)

    def _apply_prefix_chain(self,c,chain,weight,derived_penalty):
        out=c
        # Apply inside-out so ``['و','لل']`` becomes و@ل@ال@LEXEME.
        for pref in reversed(chain):
            lp,pp,rp=PREFIX_MAP[pref]
            out=self._compose(out,lp,pp,rp,'clitic_prefix',weight+derived_penalty,pref)
        return out

    @staticmethod
    def _suffix_base_variants(base:str):
        """Recover written stem alternations before attached pronouns."""
        out=[base]
        # حريته -> حرية + ه (tāʾ marbūṭa surfaces as ت before suffixes).
        if base.endswith('ت') and len(base)>1:
            out.append(base[:-1]+'ة')
        # أقصاه -> أقصى + ه; common legacy/manuscript attachment spelling.
        if base.endswith('ا') and len(base)>1:
            out.append(base[:-1]+'ى')
        # Final hamza changes seat before attached pronouns according to case:
        # بقاء -> بقائه / بقاؤه (and بقاءه in the accusative spelling).
        # Recover the bare lexical form ending in standalone hamza so that
        # standard suffixed spellings can be validated without requiring the
        # legacy lexicon to enumerate every case-conditioned hamza seat.
        if base.endswith(('ئ','ؤ')) and len(base)>1:
            out.append(base[:-1]+'ء')
        return list(dict.fromkeys(out))

    def analyze_with_clitics(self,surface:str,historical=True):
        out=self.base_candidates(surface,historical)
        derived_penalty=-4.0 if out else 0.0
        s=skeleton(surface)

        paths=list(self._prefix_paths(s,3))
        # Prefix chains, including nested conjunction/preposition/article forms.
        for chain,base in paths:
            for c in self.base_candidates(base,historical):
                if not article_compatible_candidate(chain,c):
                    continue
                w=1.6 if len(chain)==1 else 1.15
                out.append(self._apply_prefix_chain(c,chain,w,derived_penalty))

        # Suffix-only, including stem alternations before attached pronouns.
        for suf in DMT_SUFFIXES:
            if s.endswith(suf) and len(s)>len(suf)+1:
                written_base=s[:-len(suf)]
                for base in self._suffix_base_variants(written_base):
                    for c in self.base_candidates(base,historical):
                        out.append(self._suffix(c,suf,'pronominal_suffix',1.6+derived_penalty))
                break

        # Prefix-chain + suffix, required for forms such as فلعمري.
        for suf in DMT_SUFFIXES:
            if not (s.endswith(suf) and len(s)>len(suf)+2):continue
            without_suffix=s[:-len(suf)]
            for chain,stem in self._prefix_paths(without_suffix,3):
                for stem_variant in self._suffix_base_variants(stem):
                    for c in self.base_candidates(stem_variant,historical):
                        if not article_compatible_candidate(chain,c):
                            continue
                        pc=self._apply_prefix_chain(c,chain,1.05,derived_penalty)
                        out.append(self._suffix(pc,suf,'pronominal_suffix',1.05+derived_penalty))
            break
        return self._dedup(out)
    def _cand(self,e,source,weight,form,surface,is_hist):
        rules=explain_match(surface,form) if is_hist else []
        return Candidate(e.lemma,e.pos,e.root,e.secondary,tag_from_composite_pos(e.pos),weight,
                         [Evidence(source,f'{e.file}; matched={form}',weight)],
                         [label(r) for r in rules])
    def _compose(self,c,lp,pp,rp,source,weight,pref):
        return Candidate(lp+c.lemma,pp+c.pos,rp+c.root,c.secondary,tag_from_composite_pos(pp+c.pos),c.score+weight,
                         c.evidence+[Evidence(source,f'prefix={pref}',weight)],list(c.historical_rules))
    def _suffix(self,c,suf,source,weight):
        return Candidate(c.lemma+'@'+suf,c.pos+'@DMT',c.root+'@'+suf,c.secondary,tag_from_composite_pos(c.pos),c.score+weight,
                         c.evidence+[Evidence(source,f'suffix={suf}',weight)],list(c.historical_rules))
    @staticmethod
    def _dedup(cs):
        best={}
        for c in cs:
            k=c.key
            if k not in best or c.score>best[k].score:best[k]=c
            elif k in best:
                # retain distinct provenance
                seen={(e.source,e.detail) for e in best[k].evidence}
                best[k].evidence.extend(e for e in c.evidence if (e.source,e.detail) not in seen)
                best[k].historical_rules=sorted(set(best[k].historical_rules+c.historical_rules))
        return sorted(best.values(),key=lambda x:(-x.score,x.pos,x.lemma))
