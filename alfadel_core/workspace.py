from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json, os, tempfile, uuid


def default_workspace_path() -> Path:
    if os.name == 'nt':
        base = Path(os.getenv('APPDATA') or os.getenv('LOCALAPPDATA') or Path.home()) / 'ALFADEL'
    else:
        base = Path(os.getenv('XDG_CONFIG_HOME') or (Path.home() / '.config')) / 'alfadel'
    return base / 'workspace.json'


def _project_stats(project: dict) -> dict:
    tokens = project.get('tokens') or [] if isinstance(project, dict) else []
    return {
        'tokens': len(tokens),
        'manual': sum(bool(t.get('manual_selected')) for t in tokens if isinstance(t, dict)),
        'unresolved': sum(not (t.get('candidates') or []) for t in tokens if isinstance(t, dict)),
        'high_review': sum(t.get('review_priority') == 'high' for t in tokens if isinstance(t, dict)),
        'medium_review': sum(t.get('review_priority') == 'medium' for t in tokens if isinstance(t, dict)),
    }


class WorkspaceStore:
    """Persistent multi-document workspace, separate from frozen external lexical resources.

    A workspace stores complete portable ALFADEL project snapshots.  It does not
    silently install project-local lexicon/memory snapshots into the live user
    profile; those remain separately controlled resources.
    """
    VERSION = 1

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_workspace_path()
        self.documents: list[dict] = []
        self.load()

    def __len__(self):
        return len(self.documents)

    def load(self):
        self.documents = []
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or raw.get('format') != 'ALFADEL_WORKSPACE':
                return
            for d in raw.get('documents') or []:
                if not isinstance(d, dict) or not isinstance(d.get('project'), dict):
                    continue
                if d['project'].get('format') != 'ALFADEL_PROJECT' or not isinstance(d['project'].get('tokens'), list):
                    continue
                self.documents.append({
                    'id': str(d.get('id') or uuid.uuid4().hex),
                    'name': str(d.get('name') or 'Untitled document').strip() or 'Untitled document',
                    'updated_at': str(d.get('updated_at') or ''),
                    'project': d['project'],
                })
        except Exception:
            self.documents = []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict(include_path=False)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix='workspace_', suffix='.json', dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def upsert(self, project: dict, name: str = '', doc_id: str = '') -> dict:
        if not isinstance(project, dict) or project.get('format') != 'ALFADEL_PROJECT' or not isinstance(project.get('tokens'), list):
            raise ValueError('Not a valid ALFADEL project')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        doc_id = str(doc_id or '').strip()
        if doc_id:
            for d in self.documents:
                if d['id'] == doc_id:
                    d['project'] = project
                    d['name'] = (name or d['name'] or 'Untitled document').strip() or 'Untitled document'
                    d['updated_at'] = now
                    self.save()
                    return self._summary(d, include_project=True)
        d = {
            'id': doc_id or uuid.uuid4().hex,
            'name': (name or project.get('name') or 'Untitled document').strip() or 'Untitled document',
            'updated_at': now,
            'project': project,
        }
        self.documents.append(d)
        self.save()
        return self._summary(d, include_project=True)

    def remove(self, doc_id: str) -> bool:
        before = len(self.documents)
        self.documents = [d for d in self.documents if d['id'] != str(doc_id)]
        changed = len(self.documents) != before
        if changed:
            self.save()
        return changed

    def rename(self, doc_id: str, name: str) -> bool:
        name = (name or '').strip()
        if not name:
            raise ValueError('Document name is required')
        for d in self.documents:
            if d['id'] == str(doc_id):
                d['name'] = name
                d['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
                self.save()
                return True
        return False

    def clear(self):
        self.documents = []
        self.save()

    def import_payload(self, payload: dict, mode: str = 'merge') -> int:
        if not isinstance(payload, dict) or payload.get('format') != 'ALFADEL_WORKSPACE':
            raise ValueError('Not an ALFADEL workspace file')
        rows = payload.get('documents') or []
        if not isinstance(rows, list):
            raise ValueError('documents must be a list')
        if mode not in {'merge', 'replace'}:
            raise ValueError('mode must be merge or replace')
        if mode == 'replace':
            self.documents = []
        before = len(self.documents)
        by_id = {d['id']: d for d in self.documents}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('project'), dict):
                continue
            p = row['project']
            if p.get('format') != 'ALFADEL_PROJECT' or not isinstance(p.get('tokens'), list):
                continue
            did = str(row.get('id') or uuid.uuid4().hex)
            neo = {
                'id': did,
                'name': str(row.get('name') or 'Untitled document').strip() or 'Untitled document',
                'updated_at': str(row.get('updated_at') or datetime.now(timezone.utc).isoformat(timespec='seconds')),
                'project': p,
            }
            if did in by_id:
                by_id[did].update(neo)
            else:
                self.documents.append(neo); by_id[did] = neo
        self.save()
        return len(self.documents) - (0 if mode == 'replace' else before)

    @staticmethod
    def _summary(d: dict, include_project: bool = False) -> dict:
        out = {'id': d['id'], 'name': d['name'], 'updated_at': d.get('updated_at', ''), **_project_stats(d['project'])}
        if include_project:
            out['project'] = d['project']
        return out

    def stats(self) -> dict:
        docs = [self._summary(d) for d in self.documents]
        totals = {'documents': len(docs), 'tokens': 0, 'manual': 0, 'unresolved': 0, 'high_review': 0, 'medium_review': 0}
        for d in docs:
            for k in ['tokens', 'manual', 'unresolved', 'high_review', 'medium_review']:
                totals[k] += d[k]
        return totals

    def to_dict(self, include_path: bool = True, include_projects: bool = True) -> dict:
        payload = {'format': 'ALFADEL_WORKSPACE', 'version': self.VERSION, 'documents': []}
        if include_path:
            payload['path'] = str(self.path)
        for d in self.documents:
            payload['documents'].append(self._summary(d, include_project=include_projects))
        payload['stats'] = self.stats()
        return payload
