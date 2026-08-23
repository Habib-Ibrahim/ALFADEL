"""Legacy orthographic exceptions, restored as an auditable native resource.

The 2008 installation shipped ``Lexicon/Ortografia.adz``.  It is not part of the
hash-locked Stage-13 production lexicon, so ALFADEL core keeps it in its own resource
folder and uses it only in *native* mode.  Each match is recorded as provenance.
"""
from __future__ import annotations
from pathlib import Path
import collections
from .orthography import strip_diacritics

class LegacyOrthographyMap:
    def __init__(self,path:Path):
        self.path=Path(path)
        self.map=collections.defaultdict(list)
        self.rows=0
        if self.path.exists(): self._load()
    def _load(self):
        text=self.path.read_text(encoding='cp1256',errors='replace')
        for line in text.splitlines():
            f=[x.strip() for x in line.split('\t')]
            if len(f)<2 or not f[0] or not f[1]: continue
            surface=strip_diacritics(f[0]); canonical=strip_diacritics(f[1])
            if not surface or not canonical: continue
            if canonical not in self.map[surface]: self.map[surface].append(canonical)
            self.rows+=1
    def canonical_forms(self,surface:str):
        return list(self.map.get(strip_diacritics(surface),()))
    def __len__(self): return self.rows
