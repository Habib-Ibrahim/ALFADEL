from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone
import json, os, tempfile
from .orthography import strip_diacritics


def default_correction_memory_path() -> Path:
    if os.name == 'nt':
        base = Path(os.getenv('APPDATA') or os.getenv('LOCALAPPDATA') or Path.home()) / 'ALFADEL'
    else:
        base = Path(os.getenv('XDG_CONFIG_HOME') or (Path.home() / '.config')) / 'alfadel'
    return base / 'human_corrections.json'


def _norm(x: str) -> str:
    return strip_diacritics(x or '').strip()


@dataclass
class CorrectionEvent:
    surface: str
    lemma: str
    pos: str
    root: str = ''
    broad_pos: str = 'OTHER'
    left_context: list[str] | None = None
    right_context: list[str] | None = None
    note: str = ''
    created_at: str = ''

    @property
    def key(self):
        return (self.lemma, self.pos, self.root)


class CorrectionMemory:
    """Persistent contextual memory built only from explicit human corrections.

    This is deliberately separate from the user lexicon.  It never generates a
    lexical analysis.  It may only re-rank a candidate that the native lexical
    engine already generated, and only when the stored local context matches
    conservatively.
    """
    VERSION = 1

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_correction_memory_path()
        self.events: list[CorrectionEvent] = []
        self.by_surface: dict[str, list[CorrectionEvent]] = {}
        self.load()

    def __len__(self):
        return len(self.events)

    def _reindex(self):
        d: dict[str, list[CorrectionEvent]] = {}
        for e in self.events:
            d.setdefault(_norm(e.surface), []).append(e)
        self.by_surface = d

    def load(self):
        self.events = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                rows = raw.get('events', []) if isinstance(raw, dict) else []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    surface = (r.get('surface') or '').strip()
                    lemma = (r.get('lemma') or '').strip()
                    pos = (r.get('pos') or '').strip()
                    if not surface or not lemma or not pos:
                        continue
                    self.events.append(CorrectionEvent(
                        surface=surface, lemma=lemma, pos=pos,
                        root=(r.get('root') or '').strip(),
                        broad_pos=(r.get('broad_pos') or 'OTHER').strip(),
                        left_context=[str(x) for x in (r.get('left_context') or [])][-2:],
                        right_context=[str(x) for x in (r.get('right_context') or [])][:2],
                        note=(r.get('note') or '').strip(),
                        created_at=(r.get('created_at') or '')
                    ))
            except Exception:
                self.events = []
        self._reindex()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'format':'ALFADEL_CORRECTION_MEMORY','version':self.VERSION,
                   'events':[asdict(e) for e in self.events]}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix='human_corrections_', suffix='.json', dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def add(self, surface: str, lemma: str, pos: str, root: str='', broad_pos: str='OTHER',
            left_context=None, right_context=None, note: str='') -> CorrectionEvent:
        surface=(surface or '').strip(); lemma=(lemma or '').strip(); pos=(pos or '').strip(); root=(root or '').strip()
        if not surface or not lemma or not pos:
            raise ValueError('surface, lemma and POS are required')
        e = CorrectionEvent(
            surface=surface, lemma=lemma, pos=pos, root=root,
            broad_pos=(broad_pos or 'OTHER').strip(),
            left_context=[str(x) for x in (left_context or [])][-2:],
            right_context=[str(x) for x in (right_context or [])][:2],
            note=(note or '').strip(),
            created_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        )
        # Identical repeated confirmations in the exact same local context are
        # useful evidence, but accidental duplicate clicks are not.  De-duplicate
        # an exact event signature.
        sig = self._signature(e)
        if not any(self._signature(x) == sig for x in self.events):
            self.events.append(e); self._reindex(); self.save()
        return e

    def add_many(self, rows: list[dict]) -> int:
        before=len(self.events)
        for r in rows:
            if not isinstance(r, dict): continue
            try:
                self.add(
                    surface=r.get('surface',''), lemma=r.get('lemma',''), pos=r.get('pos',''), root=r.get('root',''),
                    broad_pos=r.get('broad_pos','OTHER'), left_context=r.get('left_context') or [],
                    right_context=r.get('right_context') or [], note=r.get('note','human-confirmed correction')
                )
            except ValueError:
                continue
        return len(self.events)-before

    @staticmethod
    def _signature(e: CorrectionEvent):
        return (_norm(e.surface), e.lemma, e.pos, e.root,
                tuple(_norm(x) for x in (e.left_context or [])),
                tuple(_norm(x) for x in (e.right_context or [])))

    def to_dict(self):
        return {'format':'ALFADEL_CORRECTION_MEMORY','version':self.VERSION,'path':str(self.path),
                'events':[asdict(e) for e in self.events]}

    def import_payload(self, payload: dict, mode: str='merge') -> int:
        if not isinstance(payload, dict) or payload.get('format') != 'ALFADEL_CORRECTION_MEMORY':
            raise ValueError('Not an ALFADEL correction-memory file')
        rows=payload.get('events',[])
        if not isinstance(rows,list): raise ValueError('events must be a list')
        if mode == 'replace':
            self.events=[]; self._reindex(); self.save()
        return self.add_many(rows)

    def clear(self):
        self.events=[]; self._reindex(); self.save()

    def remove_event(self, row: dict) -> bool:
        try:
            probe=CorrectionEvent(
                surface=(row.get('surface') or '').strip(), lemma=(row.get('lemma') or '').strip(),
                pos=(row.get('pos') or '').strip(), root=(row.get('root') or '').strip(),
                broad_pos=(row.get('broad_pos') or 'OTHER').strip(),
                left_context=[str(x) for x in (row.get('left_context') or [])][-2:],
                right_context=[str(x) for x in (row.get('right_context') or [])][:2])
        except Exception:
            return False
        sig=self._signature(probe); before=len(self.events)
        self.events=[e for e in self.events if self._signature(e)!=sig]
        changed=len(self.events)!=before
        if changed: self._reindex(); self.save()
        return changed

    def surface_count(self, surface: str) -> int:
        return len(self.by_surface.get(_norm(surface), []))

    def candidate_support(self, tokens: list[str], index: int, candidates) -> tuple[list[float], list[str], bool]:
        """Return conservative contextual support for existing candidates.

        A single stored correction may automatically influence selection only if
        both immediate context sides that existed in the stored event match, or
        an exact two-token side at a text boundary matches.  Surface-only memory
        never changes the automatic choice.
        """
        scores=[0.0 for _ in candidates]; explanations=['' for _ in candidates]
        if index < 0 or index >= len(tokens) or not candidates:
            return scores, explanations, False
        events=self.by_surface.get(_norm(tokens[index]), [])
        if not events:
            return scores, explanations, False
        left=[_norm(x) for x in tokens[max(0,index-2):index]]
        right=[_norm(x) for x in tokens[index+1:min(len(tokens),index+3)]]
        matched_any=False
        by_key={getattr(c,'key',None):j for j,c in enumerate(candidates)}
        for e in events:
            eleft=[_norm(x) for x in (e.left_context or [])]
            eright=[_norm(x) for x in (e.right_context or [])]
            # Require all stored local context on both available sides to match.
            left_ok = (not eleft) or left[-len(eleft):] == eleft
            right_ok = (not eright) or right[:len(eright)] == eright
            # Avoid a context-free event affecting ranking.
            context_tokens=len(eleft)+len(eright)
            if not (left_ok and right_ok and context_tokens >= 1):
                continue
            j=by_key.get(e.key)
            if j is None:
                continue
            matched_any=True
            # Exact two-sided context is strongest; boundary/one-sided exact
            # context is deliberately weaker but still visible.
            strength=8.0 if eleft and eright else 5.0
            strength += min(2.0, 0.5*max(0, context_tokens-2))
            scores[j]+=strength
            explanations[j]=(explanations[j]+'; ' if explanations[j] else '') + 'human-confirmed matching context'
        return scores, explanations, matched_any
