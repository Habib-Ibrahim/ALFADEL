from __future__ import annotations
from pathlib import Path
import json,collections
from .orthography import strip_diacritics,historical_key,fully_collapsed_key
from .models import Candidate,Evidence
from .taxonomy import tag_from_composite_pos

class TrainingEvidence:
    def __init__(self,path:Path):
        self.path=Path(path);self.exact=collections.defaultdict(collections.Counter);self.hist=collections.defaultdict(collections.Counter)
        self.stems=collections.defaultdict(collections.Counter)
        self.counts=0; self._load()
    def _load(self):
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        with self.path.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip():continue
                r=json.loads(line);work=(r.get('work') or '').strip()
                if work in {'Abou_Qurra','GRNA_Or_43_ARA','GRNA Or. 43','GRNA'}:continue
                s=(r.get('normalized_surface') or r.get('surface') or '').strip();sig=(r.get('lemma',''),(r.get('source_pos') or r.get('aaw_pos') or ''),r.get('root',''))
                self.exact[s][sig]+=1; self.hist[historical_key(s)][sig]+=1; self.counts+=1
                self._recover_attested_stem(r,s)

    _CLITIC_POS={'ART','DMT','WAW','FA','SIN','MA','nida','dmm','lamz','lamibt','nountwk','hrfgrr','hrfgrrcmplx','hrfcll'}

    def _recover_attested_stem(self,r:dict,surface:str):
        """Recover an explicitly segmented lexical stem from controlled corpus data.

        This does not invent morphology.  It only records a written lexical
        component that the controlled corpus itself separates from visible
        prefixes/suffixes.  Context-conditioned stems (e.g. ة→ت before a
        pronoun) are excluded so they are not promoted to standalone forms.
        """
        split=(r.get('split_form') or '').strip()
        pos=(r.get('source_pos') or r.get('aaw_pos') or '').strip()
        lemma=(r.get('lemma') or '').strip()
        root=(r.get('root') or '').strip()
        if '@' not in split or '@' not in pos:
            return
        sp=[strip_diacritics(x) for x in split.split('@')]
        pp=[x.strip() for x in pos.split('@')]
        lp=[x.strip() for x in lemma.split('@')]
        rp=[x.strip() for x in root.split('@')]
        if len(sp)!=len(pp):
            return
        core=[i for i,p in enumerate(pp) if p and p not in self._CLITIC_POS]
        if len(core)!=1:
            return
        i=core[0]
        # Do not promote a contextual finite-verb surface to a standalone
        # canonical stem. Verb morphology remains governed by the verb
        # paradigms (e.g. يقولو must stay historical for يقولوا).
        if tag_from_composite_pos(pp[i]) == 'VERB':
            return
        lead=''.join(sp[:i]); trail=''.join(sp[i+1:])
        surf=strip_diacritics(surface)
        if lead and not surf.startswith(lead):
            return
        if trail and not surf.endswith(trail):
            return
        a=len(lead); b=len(surf)-len(trail) if trail else len(surf)
        stem=surf[a:b]
        if not stem or len(stem)<2:
            return
        core_lemma=lp[i] if i<len(lp) else ''
        core_root=rp[i] if i<len(rp) else ''
        lem_skel=strip_diacritics(core_lemma)
        # Do not promote contextual pre-suffix spellings to standalone forms.
        if trail:
            if stem.endswith('ت') and lem_skel.endswith('ة'):
                return
            if stem.endswith('ا') and lem_skel.endswith('ى'):
                return
            if stem.endswith(('ئ','ؤ')) and lem_skel.endswith('ء'):
                return
        self.stems[stem][(core_lemma,pp[i],core_root)]+=1

    def stem_candidates(self,surface:str):
        surface=strip_diacritics(surface or '')
        ctr=self.stems.get(surface,collections.Counter())
        out=[]; total=sum(ctr.values())
        for (lemma,pos,root),count in ctr.most_common(8):
            dom=count/total if total else 0
            score=8.75+min(1.0,dom)
            out.append(Candidate(lemma,pos,root,'',tag_from_composite_pos(pos),score,
                                 [Evidence('controlled_corpus_attested_lexical_stem',f'support={count}/{total}; dominance={dom:.3f}',score)]))
        return out

    def candidates(self,surface:str,historical=True):
        surface=strip_diacritics(surface or '')
        out=[]
        def add(ctr,source,base_weight,min_count=1,min_dom=0):
            total=sum(ctr.values())
            for (lemma,pos,root),count in ctr.most_common(8):
                dom=count/total if total else 0
                if count<min_count or dom<min_dom:continue
                out.append(Candidate(lemma,pos,root,'',tag_from_composite_pos(pos),base_weight+2*dom,
                                     [Evidence(source,f'support={count}/{total}; dominance={dom:.3f}',base_weight+2*dom)]))
        add(self.exact.get(surface,collections.Counter()),'controlled_corpus_exact_surface',10.0)
        if historical:
            add(self.hist.get(historical_key(surface),collections.Counter()),'controlled_corpus_historical_key',7.5,2,.90)
        return out
