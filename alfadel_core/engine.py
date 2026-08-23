from __future__ import annotations
from pathlib import Path
import copy
from .lexicon import LexiconStore
from .morphology import MorphologyIndex
from .training import TrainingEvidence
from .tokenizer import tokenize
from .locations import tokenize_with_locations, LocatedToken
from .models import TokenAnalysis, Evidence
from .ai import configured_provider
from .ranker import NativeRanker
from .legacy_orthography import LegacyOrthographyMap
from .user_lexicon import UserLexicon
from .correction_memory import CorrectionMemory

class NativeAnalyzer:
    """Unicode-first native ALFADEL core Alpha engine.

    Alpha 12 keeps the Alpha 11 scholarly location layer and adds a scroll-following analysis inspector plus native Excel .xlsx export; Alpha 10 adds direct Word DOCX import and broader text decoding; Alpha 9 added native plain-text import while preserving the Alpha 8 multi-document corpus workspace and cross-document contextual evaluation; Alpha 7 added editable/importable personal lexica and a conservative contextual correction memory; Alpha 6 added a persistent personal user lexicon and keeps the restored 2008 ``Ortografia.adz`` exception dictionary,
    additional defective imperfect spelling support, explicit review metadata,
    and a provider-neutral JSON/HTTP AI bridge.  Stage-13 compatibility remains
    byte-for-byte separate and unchanged.
    """
    def __init__(self,resource_root:Path,user_lexicon_path:Path|None=None,correction_memory_path:Path|None=None):
        resource_root=Path(resource_root)
        self.resource_root=resource_root
        self.lexicon=LexiconStore(resource_root/'lexicon', resource_root/'reviewed_overlay'/'canonical_lexicon_additions.tsv')
        self.morph=MorphologyIndex(self.lexicon)
        training_path=resource_root/'controlled_corpus'/'controlled_corpus.jsonl'
        self.training=TrainingEvidence(training_path)
        self.ranker=NativeRanker(training_path)
        self.ai=configured_provider()
        self.legacy_orthography=LegacyOrthographyMap(resource_root/'orthography'/'Ortografia.adz')
        self.user_lexicon=UserLexicon(user_lexicon_path)
        self.correction_memory=CorrectionMemory(correction_memory_path)
        self._candidate_cache={}

    def analyze(self,text:str,historical=True,use_ai=False,use_memory=True):
        located=tokenize_with_locations(text)
        rows=self.analyze_tokens([x.surface for x in located],historical,use_ai,use_memory)
        for row,loc in zip(rows,located):
            row.page=loc.page
            row.paragraph=loc.paragraph
            row.line=loc.line
            row.source_line=loc.source_line
            row.word_in_paragraph=loc.word_in_paragraph
            row.legacy_location=loc.legacy_location
            row.mode='native-alpha12'
        return rows

    def _training_clitic_candidates(self,surface:str,historical=True):
        """Compose controlled-corpus lexical evidence with visible clitics.

        These candidates are deliberately penalized relative to an exact full-
        surface corpus hit; they add recall without silently outranking direct
        lexical evidence.
        """
        from .lexicon import skeleton
        out=[]; s=skeleton(surface)
        if self.training.candidates(s,False):
            return out
        for chain,base in self.morph._prefix_paths(s,3):
            for c in self.training.candidates(base,historical)+self.training.stem_candidates(base):
                pc=self.morph._apply_prefix_chain(c,chain,-0.55,-1.25)
                pc.evidence[-1]=type(pc.evidence[-1])('controlled_corpus_clitic_prefix',
                    'prefix_chain='+'+'.join(chain),-1.8)
                pc.score=min(pc.score,4.75)
                out.append(pc)
        from .morphology import DMT_SUFFIXES
        for suf in DMT_SUFFIXES:
            if s.endswith(suf) and len(s)>len(suf)+1:
                written=s[:-len(suf)]
                for base in self.morph._suffix_base_variants(written):
                    for c in self.training.candidates(base,historical)+self.training.stem_candidates(base):
                        sc=self.morph._suffix(c,suf,'controlled_corpus_pronominal_suffix',-1.5)
                        sc.score=min(sc.score,4.75)
                        out.append(sc)
                break
        for suf in DMT_SUFFIXES:
            if not (s.endswith(suf) and len(s)>len(suf)+2): continue
            without=s[:-len(suf)]
            for chain,stem in self.morph._prefix_paths(without,3):
                for base in self.morph._suffix_base_variants(stem):
                    for c in self.training.candidates(base,historical)+self.training.stem_candidates(base):
                        pc=self.morph._apply_prefix_chain(c,chain,-0.85,-1.25)
                        sc=self.morph._suffix(pc,suf,'controlled_corpus_prefix_suffix',-1.15)
                        sc.score=min(sc.score,4.75)
                        out.append(sc)
            break
        return self.morph._dedup(out)


    def _user_lexicon_candidates(self,surface:str,historical=True):
        """Return user-confirmed entries, including conservative clitic composition.

        The personal lexicon is intentionally outside the frozen external lexical resources.
        Exact user entries have the strongest provenance; derived clitic forms
        inherit that provenance but remain visible as composed analyses.
        """
        from .lexicon import skeleton
        from .morphology import DMT_SUFFIXES
        if not len(self.user_lexicon):
            return []
        s=skeleton(surface); out=self.user_lexicon.candidates(s,historical)
        # If the complete written surface was explicitly confirmed, do not invent
        # alternative user-lexicon segmentations of the same word.
        if any(e.source=='user_lexicon_exact' for c in out for e in c.evidence):
            return self.morph._dedup(out)
        for chain,base in self.morph._prefix_paths(s,3):
            for c in self.user_lexicon.candidates(base,historical):
                pc=self.morph._apply_prefix_chain(c,chain,1.35,-0.35)
                pc.evidence.append(Evidence('user_lexicon_clitic_composition','prefix_chain='+'+'.join(chain),1.0))
                out.append(pc)
        for suf in DMT_SUFFIXES:
            if s.endswith(suf) and len(s)>len(suf)+1:
                written=s[:-len(suf)]
                for base in self.morph._suffix_base_variants(written):
                    for c in self.user_lexicon.candidates(base,historical):
                        sc=self.morph._suffix(c,suf,'user_lexicon_pronominal_suffix',1.2)
                        out.append(sc)
                break
        for suf in DMT_SUFFIXES:
            if not (s.endswith(suf) and len(s)>len(suf)+2): continue
            without=s[:-len(suf)]
            for chain,stem in self.morph._prefix_paths(without,3):
                for base in self.morph._suffix_base_variants(stem):
                    for c in self.user_lexicon.candidates(base,historical):
                        pc=self.morph._apply_prefix_chain(c,chain,1.0,-0.35)
                        out.append(self.morph._suffix(pc,suf,'user_lexicon_prefix_suffix',1.0))
            break
        return self.morph._dedup(out)

    def add_user_lexicon_entry(self,**kwargs):
        e=self.user_lexicon.add(**kwargs); self._candidate_cache.clear(); self.ranker.support_cache.clear(); return e

    def remove_user_lexicon_entry(self,**kwargs):
        changed=self.user_lexicon.remove(**kwargs)
        if changed: self._candidate_cache.clear(); self.ranker.support_cache.clear()
        return changed

    def update_user_lexicon_entry(self,old:dict,new:dict):
        e=self.user_lexicon.update(old,new); self._candidate_cache.clear(); self.ranker.support_cache.clear(); return e

    def import_user_lexicon(self,payload:dict,mode:str='merge'):
        n=self.user_lexicon.import_payload(payload,mode); self._candidate_cache.clear(); self.ranker.support_cache.clear(); return n

    def commit_corrections(self,rows:list[dict]):
        return self.correction_memory.add_many(rows)

    def import_correction_memory(self,payload:dict,mode:str='merge'):
        return self.correction_memory.import_payload(payload,mode)

    def _legacy_orthography_candidates(self,surface:str,historical=True):
        """Recover candidates through the original 2008 Ortografia.adz map.

        The surface is never overwritten.  The canonical form is analyzed by
        the native engine, and the resulting candidates are copied back with an
        explicit provenance record.  This is only active when historical mode
        is enabled and never affects frozen Stage-13 compatibility mode.
        """
        if not historical: return []
        out=[]
        for canonical in self.legacy_orthography.canonical_forms(surface):
            # Avoid recursive use of the orthography map: use corpus + native
            # morphology directly on the mapped canonical form.
            cs=self.training.candidates(canonical,True)+self.training.stem_candidates(canonical)+self._training_clitic_candidates(canonical,True)+self.morph.analyze_with_clitics(canonical,True)
            for c in cs:
                c=copy.deepcopy(c)
                c.score=min(float(c.score),8.9)
                c.evidence.append(Evidence('legacy_orthography_dictionary',f'{surface} → {canonical}',1.5))
                c.historical_rules=sorted(set(c.historical_rules+[f'legacy orthography: {surface} → {canonical}']))
                out.append(c)
        return self.morph._dedup(out)

    @staticmethod
    def _review_metadata(cs,selected,reason,supports):
        if not cs or selected is None:
            return 0.0,'unresolved: no native candidate','high'
        if len(cs)==1:
            return 99.0,'','none'
        # Diagnostic utility, not a calibrated probability.  It mirrors what
        # the user sees: morphology score + controlled-corpus support.
        vals=[float(c.score)+(float(supports[j]) if j<len(supports) else 0.0) for j,c in enumerate(cs)]
        chosen=vals[selected]
        alt=max((v for j,v in enumerate(vals) if j!=selected),default=chosen)
        margin=chosen-alt
        base_scores=[float(c.score) for c in cs]
        top=max(base_scores)
        base_ties=sum(abs(v-top)<1e-9 for v in base_scores)
        if reason.startswith('guard_'):
            if margin<0: return margin,'structural guard overrides a higher raw score','medium'
            return margin,'','none'
        if base_ties>1 and margin<2.0:
            return margin,'near-tie between plausible candidates','medium'
        if margin<0.75:
            return margin,'low decision margin','high'
        if margin<2.0:
            return margin,'low decision margin','medium'
        if reason=='native_evidence_prior' and len(cs)>2 and margin<4.0:
            return margin,'multiple candidates without exact-surface corpus prior','medium'
        return margin,'','none'

    def analyze_tokens(self,toks,historical=True,use_ai=False,use_memory=True):
        toks=list(toks); candidate_lists=[]
        for s in toks:
            cache_key=(s,bool(historical))
            cached=self._candidate_cache.get(cache_key)
            if cached is None:
                cs=(self._user_lexicon_candidates(s,historical)+
                    self.training.candidates(s,historical)+
                    self.training.stem_candidates(s)+
                    self._training_clitic_candidates(s,historical)+
                    self.morph.analyze_with_clitics(s,historical)+
                    self._legacy_orthography_candidates(s,historical))
                best={}
                for c in cs:
                    k=c.key
                    if k not in best or c.score>best[k].score:
                        best[k]=c
                    else:
                        seen={(e.source,e.detail) for e in best[k].evidence}
                        best[k].evidence.extend(e for e in c.evidence if (e.source,e.detail) not in seen)
                        best[k].historical_rules=sorted(set(best[k].historical_rules+c.historical_rules))
                cached=sorted(best.values(),key=lambda c:(-c.score,c.pos,c.lemma))
                self._candidate_cache[cache_key]=cached
            candidate_lists.append(copy.deepcopy(cached))

        ranks,supports,reasons,_=self.ranker.rank(toks,candidate_lists)
        for cs,ss in zip(candidate_lists,supports):
            for j,c in enumerate(cs):
                c.rank_support=ss[j] if j < len(ss) else 0.0

        # Alpha 7: contextual memory is built only from explicit human
        # corrections. It never generates morphology and only overrides the
        # symbolic winner when a stored local context matches conservatively.
        memory_surface_counts=[]
        for i,cs in enumerate(candidate_lists):
            if use_memory:
                ms,mex,matched=self.correction_memory.candidate_support(toks,i,cs)
                memory_surface_counts.append(self.correction_memory.surface_count(toks[i]))
            else:
                ms=[0.0 for _ in cs]; mex=['' for _ in cs]; matched=False; memory_surface_counts.append(0)
            for j,c in enumerate(cs):
                c.correction_support=ms[j] if j < len(ms) else 0.0
                c.correction_explanation=mex[j] if j < len(mex) else ''
            if cs and ms:
                best=max(ms); contenders=[j for j,v in enumerate(ms) if abs(v-best)<1e-9 and v>0]
                if best>=5.0 and len(contenders)==1:
                    nr=contenders[0]
                    if ranks[i]!=nr:
                        ranks[i]=nr; reasons[i]='human_correction_memory'

        if use_ai and self.ai.available():
            for i,cs in enumerate(candidate_lists):
                if not cs or reasons[i]=='human_correction_memory': continue
                res=self.ai.rerank(toks,i,[c.to_dict() for c in cs]); scores=res.get('scores',{}); ex=res.get('explanations',{})
                for j,c in enumerate(cs):
                    adj=float(scores.get(str(j),0.0)); c.ai_score=adj; c.ai_explanation=ex.get(str(j),'')
                sym=ranks[i] if ranks[i] is not None else 0
                vals=[(float(c.ai_score or 0.0)+(0.25 if j==sym else 0.0),-j) for j,c in enumerate(cs)]
                nr=max(range(len(cs)),key=lambda j:vals[j])
                if nr!=sym:
                    ranks[i]=nr; reasons[i]='ai_candidate_rerank'

        out=[]
        for i,(s,cs,r,ss,reason) in enumerate(zip(toks,candidate_lists,ranks,supports,reasons),1):
            support=ss[r] if r is not None and r<len(ss) else 0.0
            margin,review_reason,priority=self._review_metadata(cs,r,reason,ss)
            mem_count=memory_surface_counts[i-1] if i-1 < len(memory_surface_counts) else 0
            if reason=='human_correction_memory':
                vals=[float(c.score)+float(c.rank_support or 0.0)+float(c.correction_support or 0.0) for c in cs]
                if r is not None and vals:
                    chosen=vals[r]; alt=max((v for j,v in enumerate(vals) if j!=r),default=chosen); margin=chosen-alt
                review_reason=''; priority='none'
            elif mem_count:
                note='surface was human-corrected previously in another context'
                review_reason=(review_reason+'; '+note) if review_reason else note
                if priority=='none': priority='medium'
            out.append(TokenAnalysis(i,s,cs,r,'native-alpha12',reason,support,margin,review_reason,priority))
        return out
