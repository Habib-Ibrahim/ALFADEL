from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import collections
from .orthography import strip_diacritics, historical_key, fully_collapsed_key, delete_one_internal_alif_keys

@dataclass(frozen=True)
class LexiconEntry:
    file:str; root:str; lemma:str; secondary:str; pos:str; translation:str=""

LEX_FILES={
    'Nouns.adz':'nominal','Adjectives.adz':'nominal','ProperNouns.adz':'nominal',
    'Masadir.adz':'nominal','SostAgg.adz':'nominal','Avv.adz':'nominal',
    'Prep.adz':'nominal','Pron.adz':'nominal','Numbers.adz':'nominal',
    'Verbi.adz':'verb','PassiveVerbs.adz':'verb'
}

def skeleton(s:str)->str:
    """Return the written, unvocalized Arabic skeleton.

    Arabic shadda marks gemination but does not add a second written consonant.
    Alpha 1 expanded shadda before removing diacritics, which created artificial
    forms such as ``مهذذب`` from ``مُهَذِّب`` and blocked ordinary inflections
    such as ``مهذبا``.  Alpha 2 preserves the manuscript/lexical orthography and
    simply removes vocalization and tatweel.
    """
    return strip_diacritics(s or '')

def norm(s:str)->str:
    # Exact/native key preserves hamza seats and historical spelling distinctions.
    # Those are handled separately by historical_key so provenance remains visible.
    return strip_diacritics(s)

class LexiconStore:
    def __init__(self, directory:Path, additions_path:Path|None=None):
        self.directory=Path(directory); self.additions_path=Path(additions_path) if additions_path else None; self.entries=[]
        self.exact=collections.defaultdict(list)
        self.historical=collections.defaultdict(list)
        self.one_alif_deleted=collections.defaultdict(list)
        self.kind={}
        self.load()
    def _index_entry(self,e:LexiconEntry):
        self.entries.append(e)
        for form in (e.lemma,e.secondary):
            if form and form!='-':
                self.exact[norm(form)].append((e,form))
                hk=historical_key(form)
                self.historical[hk].append((e,form))
                for dk in delete_one_internal_alif_keys(form):
                    self.one_alif_deleted[dk].append((e,form))

    def load(self):
        for fname,kind in LEX_FILES.items():
            p=self.directory/fname
            if not p.exists():continue
            self.kind[fname]=kind
            for line in p.read_text(encoding='cp1256',errors='replace').splitlines():
                f=line.split('\t')
                if len(f)<4:continue
                root,lemma,secondary,pos=(x.strip() for x in f[:4])
                if not lemma or lemma=='-' or not pos:continue
                tr=f[7].strip() if len(f)>7 else ''
                self._index_entry(LexiconEntry(fname,root,lemma,secondary or '-',pos,tr))

        # Modern correction-layer additions are intentionally kept outside the
        # frozen Stage-13 lexicon.  Each row is an explicitly reviewed lexical
        # relation, not a generated paradigm rule.
        if self.additions_path and self.additions_path.exists():
            for line in self.additions_path.read_text(encoding='utf-8-sig').splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                f=line.split('\t')
                if len(f)<5:
                    continue
                fname,root,lemma,secondary,pos=(x.strip() for x in f[:5])
                tr=f[5].strip() if len(f)>5 else ''
                if not lemma or lemma=='-' or not pos:
                    continue
                self.kind.setdefault(fname, LEX_FILES.get(fname,'nominal'))
                self._index_entry(LexiconEntry(fname,root,lemma,secondary or '-',pos,tr))
