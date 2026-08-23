from __future__ import annotations
from collections import defaultdict
from .correction_memory import CorrectionMemory, CorrectionEvent


def _candidate_key(c):
    return (str(c.get('lemma') or ''), str(c.get('pos') or ''), str(c.get('root') or ''))


def _manual_rows(project: dict):
    toks = project.get('tokens') or []
    out = []
    surfaces = [str(t.get('surface') or '') for t in toks]
    for i, t in enumerate(toks):
        if not isinstance(t, dict) or not t.get('manual_selected'):
            continue
        sel = t.get('selected')
        cs = t.get('candidates') or []
        if not isinstance(sel, int) or sel < 0 or sel >= len(cs):
            continue
        c = cs[sel]
        out.append({
            'token_index': i,
            'surface': str(t.get('surface') or ''),
            'gold_key': _candidate_key(c),
            'left_context': surfaces[max(0, i-2):i],
            'right_context': surfaces[i+1:min(len(surfaces), i+3)],
            'broad_pos': str(c.get('broad_pos') or 'OTHER'),
        })
    return out


def evaluate_workspace_context(documents: list[dict], ai_provider=None, use_ai: bool = False, max_ai_tokens: int = 200) -> dict:
    """Cross-document evaluation against explicit human selections.

    Each target document is evaluated using correction events from *other*
    workspace documents only.  This avoids evaluating a correction against the
    same occurrence from which it was learned.  Results are therefore a
    transfer diagnostic, not a general accuracy estimate.
    """
    docs = [d for d in documents if isinstance(d, dict) and isinstance(d.get('project'), dict)]
    manual_by_doc = {d.get('id', str(i)): _manual_rows(d['project']) for i, d in enumerate(docs)}
    result = {
        'format': 'ALFADEL_CONTEXT_EVALUATION', 'version': 1,
        'documents': len(docs), 'human_gold_tokens': 0,
        'baseline_correct': 0, 'memory_correct': 0, 'memory_applied': 0,
        'memory_improvements': 0, 'memory_regressions': 0,
        'same_surface_elsewhere': 0, 'exact_context_transfer_opportunities': 0,
        'ai_requested': bool(use_ai), 'ai_available': bool(ai_provider and ai_provider.available()),
        'ai_evaluated': 0, 'ai_correct': 0, 'ai_improvements': 0, 'ai_regressions': 0,
        'rows': [],
        'note': 'Cross-document diagnostic over explicit manual selections only. Correction memory for each target document is built only from other workspace documents; this is not an independent corpus accuracy estimate.'
    }
    ai_budget = max(0, int(max_ai_tokens))

    for di, d in enumerate(docs):
        project = d['project']; toks = project.get('tokens') or []
        surfaces = [str(t.get('surface') or '') for t in toks]
        target_manual = manual_by_doc.get(d.get('id', str(di)), [])
        if not target_manual:
            continue
        # Build an in-memory memory store from other documents only.
        mem = CorrectionMemory.__new__(CorrectionMemory)
        mem.path = None; mem.events = []; mem.by_surface = {}
        other_surfaces = defaultdict(int)
        for dj, od in enumerate(docs):
            if dj == di:
                continue
            for mr in manual_by_doc.get(od.get('id', str(dj)), []):
                mem.events.append(CorrectionEvent(
                    surface=mr['surface'], lemma=mr['gold_key'][0], pos=mr['gold_key'][1], root=mr['gold_key'][2],
                    broad_pos=mr['broad_pos'], left_context=mr['left_context'], right_context=mr['right_context'],
                    note='workspace cross-document evaluation', created_at=''))
                other_surfaces[mr['surface']] += 1
        mem._reindex()

        for mr in target_manual:
            i = mr['token_index']; t = toks[i]; cs = t.get('candidates') or []; gold = mr['gold_key']
            if not cs:
                continue
            keys = [_candidate_key(c) for c in cs]
            if gold not in keys:
                continue
            result['human_gold_tokens'] += 1
            auto = t.get('automatic_selected')
            if not isinstance(auto, int) or not (0 <= auto < len(cs)):
                auto = t.get('selected') if isinstance(t.get('selected'), int) else 0
            baseline_key = keys[auto] if 0 <= auto < len(keys) else None
            baseline_ok = baseline_key == gold
            result['baseline_correct'] += baseline_ok
            if other_surfaces.get(mr['surface']):
                result['same_surface_elsewhere'] += 1

            # CandidateSupport expects objects with .key; tiny adapter avoids
            # importing the full analyzer or regenerating the candidate lattice.
            class C:
                def __init__(self, k): self.key = k
            scores, explanations, matched = mem.candidate_support(surfaces, i, [C(k) for k in keys])
            mem_sel = auto
            if scores:
                best = max(scores); contenders = [j for j, v in enumerate(scores) if v > 0 and abs(v-best) < 1e-9]
                if best >= 5.0 and len(contenders) == 1:
                    mem_sel = contenders[0]; result['memory_applied'] += 1
                    result['exact_context_transfer_opportunities'] += 1
            memory_ok = keys[mem_sel] == gold if 0 <= mem_sel < len(keys) else False
            result['memory_correct'] += memory_ok
            result['memory_improvements'] += (not baseline_ok and memory_ok)
            result['memory_regressions'] += (baseline_ok and not memory_ok)

            row = {'document': d.get('name',''), 'token_index': i+1, 'surface': mr['surface'],
                   'baseline_correct': baseline_ok, 'memory_applied': mem_sel != auto,
                   'memory_correct': memory_ok, 'gold': {'lemma':gold[0],'pos':gold[1],'root':gold[2]}}

            if use_ai and ai_provider and ai_provider.available() and result['ai_evaluated'] < ai_budget:
                res = ai_provider.rerank(surfaces, i, cs)
                scores_ai = res.get('scores', {})
                vals=[]
                for j in range(len(cs)):
                    try: v=float(scores_ai.get(str(j), 0.0))
                    except Exception: v=0.0
                    vals.append((v + (0.25 if j==auto else 0.0), -j))
                ai_sel=max(range(len(cs)), key=lambda j: vals[j])
                ai_ok=keys[ai_sel]==gold
                result['ai_evaluated'] += 1; result['ai_correct'] += ai_ok
                result['ai_improvements'] += (not baseline_ok and ai_ok)
                result['ai_regressions'] += (baseline_ok and not ai_ok)
                row.update({'ai_correct':ai_ok,'ai_selected':ai_sel})
            result['rows'].append(row)
    return result
