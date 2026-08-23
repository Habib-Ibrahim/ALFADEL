from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone
import collections, json, os, tempfile
from .orthography import strip_diacritics, historical_key
from .models import Candidate, Evidence
from .taxonomy import tag_from_composite_pos


def default_user_lexicon_path() -> Path:
    """Return a per-user writable location without touching frozen resources."""
    if os.name == 'nt':
        base = Path(os.getenv('APPDATA') or os.getenv('LOCALAPPDATA') or Path.home()) / 'ALFADEL'
    else:
        base = Path(os.getenv('XDG_CONFIG_HOME') or (Path.home() / '.config')) / 'alfadel'
    return base / 'user_lexicon.json'


@dataclass
class UserLexiconEntry:
    surface: str
    lemma: str
    pos: str
    root: str = ''
    broad_pos: str = 'OTHER'
    historical_matching: bool = True
    note: str = ''
    created_at: str = ''

    @property
    def key(self):
        return (strip_diacritics(self.surface), self.lemma, self.pos, self.root)


class UserLexicon:
    """Persistent, auditable personal lexicon for confirmed scholarly analyses."""
    VERSION = 1

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_user_lexicon_path()
        self.entries: list[UserLexiconEntry] = []
        self.exact = collections.defaultdict(list)
        self.historical = collections.defaultdict(list)
        self.load()

    def __len__(self):
        return len(self.entries)

    def _reindex(self):
        self.exact.clear(); self.historical.clear()
        for e in self.entries:
            s = strip_diacritics(e.surface)
            self.exact[s].append(e)
            if e.historical_matching:
                self.historical[historical_key(s)].append(e)

    def load(self):
        self.entries = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                rows = raw.get('entries', []) if isinstance(raw, dict) else []
                for r in rows:
                    if not isinstance(r, dict): continue
                    surface=(r.get('surface') or '').strip(); lemma=(r.get('lemma') or '').strip(); pos=(r.get('pos') or '').strip()
                    if not surface or not lemma or not pos: continue
                    self.entries.append(UserLexiconEntry(
                        surface=surface, lemma=lemma, pos=pos,
                        root=(r.get('root') or '').strip(),
                        broad_pos=(r.get('broad_pos') or tag_from_composite_pos(pos) or 'OTHER'),
                        historical_matching=bool(r.get('historical_matching', True)),
                        note=(r.get('note') or '').strip(),
                        created_at=(r.get('created_at') or '')
                    ))
            except Exception:
                # A malformed personal file must never prevent ALFADEL from starting.
                self.entries=[]
        self._reindex()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload={'format':'ALFADEL_USER_LEXICON','version':self.VERSION,'entries':[asdict(e) for e in self.entries]}
        text=json.dumps(payload, ensure_ascii=False, indent=2)
        fd,tmp=tempfile.mkstemp(prefix='user_lexicon_',suffix='.json',dir=str(self.path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(text)
            os.replace(tmp,self.path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def add(self, surface: str, lemma: str, pos: str, root: str='', broad_pos: str='', historical_matching: bool=True, note: str=''):
        surface=(surface or '').strip(); lemma=(lemma or '').strip(); pos=(pos or '').strip(); root=(root or '').strip()
        if not surface or not lemma or not pos:
            raise ValueError('surface, lemma and POS are required')
        broad=(broad_pos or tag_from_composite_pos(pos) or 'OTHER').strip()
        created=datetime.now(timezone.utc).isoformat(timespec='seconds')
        new=UserLexiconEntry(surface,lemma,pos,root,broad,bool(historical_matching),(note or '').strip(),created)
        # Replace the same exact analysis rather than accumulating duplicates.
        self.entries=[e for e in self.entries if e.key != new.key]
        self.entries.append(new); self._reindex(); self.save(); return new

    def remove(self, surface: str, lemma: str, pos: str, root: str='') -> bool:
        key=(strip_diacritics(surface or ''),(lemma or '').strip(),(pos or '').strip(),(root or '').strip())
        before=len(self.entries); self.entries=[e for e in self.entries if e.key != key]
        changed=len(self.entries)!=before
        if changed: self._reindex(); self.save()
        return changed


    def update(self, old: dict, new: dict):
        """Replace one entry atomically, identified by its old analysis key."""
        old_surface=(old.get('surface') or '').strip(); old_lemma=(old.get('lemma') or '').strip(); old_pos=(old.get('pos') or '').strip(); old_root=(old.get('root') or '').strip()
        key=(strip_diacritics(old_surface),old_lemma,old_pos,old_root)
        before=len(self.entries)
        kept=[e for e in self.entries if e.key != key]
        if len(kept)==before:
            raise ValueError('user-lexicon entry not found')
        self.entries=kept; self._reindex()
        try:
            entry=self.add(surface=new.get('surface',''),lemma=new.get('lemma',''),pos=new.get('pos',''),root=new.get('root',''),
                           broad_pos=new.get('broad_pos',''),historical_matching=bool(new.get('historical_matching',True)),note=new.get('note',''))
        except Exception:
            self.load(); raise
        return entry

    def import_payload(self, payload: dict, mode: str='merge') -> int:
        """Import a portable ALFADEL user lexicon.  Merge is the safe default."""
        if not isinstance(payload,dict) or payload.get('format')!='ALFADEL_USER_LEXICON':
            raise ValueError('Not an ALFADEL user-lexicon file')
        rows=payload.get('entries',[])
        if not isinstance(rows,list): raise ValueError('entries must be a list')
        if mode not in {'merge','replace'}: raise ValueError('mode must be merge or replace')
        before=len(self.entries)
        if mode=='replace':
            self.entries=[]; self._reindex(); self.save(); before=0
        for r in rows:
            if not isinstance(r,dict): continue
            try:
                self.add(surface=r.get('surface',''),lemma=r.get('lemma',''),pos=r.get('pos',''),root=r.get('root',''),
                         broad_pos=r.get('broad_pos',''),historical_matching=bool(r.get('historical_matching',True)),note=r.get('note','imported'))
            except ValueError:
                continue
        return len(self.entries)-before

    def to_dict(self):
        return {'format':'ALFADEL_USER_LEXICON','version':self.VERSION,'path':str(self.path),'entries':[asdict(e) for e in self.entries]}

    def candidates(self, surface: str, historical: bool=True):
        s=strip_diacritics(surface or ''); rows=list(self.exact.get(s,[])); exact_ids={id(e) for e in rows}
        if historical:
            rows.extend(e for e in self.historical.get(historical_key(s),[]) if id(e) not in exact_ids)
        out=[]
        for e in rows:
            exact=strip_diacritics(e.surface)==s
            source='user_lexicon_exact' if exact else 'user_lexicon_historical'
            score=13.0 if exact else 9.0
            detail=f'user-confirmed entry for {e.surface}' + (f'; {e.note}' if e.note else '')
            rules=[] if exact else [f'user lexicon historical match: {surface} → {e.surface}']
            out.append(Candidate(e.lemma,e.pos,e.root,'',e.broad_pos,score,[Evidence(source,detail,score)],rules))
        return out
