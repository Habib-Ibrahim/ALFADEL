from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math
import re
import unicodedata

from alfadel_core.engine import NativeAnalyzer
from alfadel_core.lexicon import skeleton
from alfadel_core.morphology import DMT_SUFFIXES
from alfadel_core.orthography import (
    HAMZA_ALIF,
    historical_key,
    is_one_alif_variant,
    single_redundant_letter_variants,
    strip_diacritics,
    explain_match,
)

try:
    from rapidfuzz import process, fuzz
except Exception:  # pragma: no cover - optional fallback
    process = None
    fuzz = None

ARABIC_TOKEN_RE = re.compile(
    r"[\u0621-\u063A\u0641-\u064A\u066E-\u06D3\u06FA-\u06FC\u0750-\u077F\u08A0-\u08C9]"
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0621-\u063A\u0641-\u064A\u066E-\u06D3\u06FA-\u06FC\u0750-\u077F\u08A0-\u08C9]*"
)

# OCR/HTR shape families. Historical conventions are handled separately below.
SHAPE_GROUPS = [
    set("بتثني"),
    set("جحخ"),
    set("دذ"),
    set("رز"),
    set("سش"),
    set("صض"),
    set("طظ"),
    set("عغ"),
    set("فق"),
]
ARABIC_ALPHABET = tuple("ابتثجحخدذرزسشصضطظعغفقكلمنهويءأةإآؤئى")

HISTORICAL_LABELS = {
    "TA_MARBUTA_AS_HA": "Possible historical form: tāʾ marbūṭa written as hāʾ (ه for ة).",
    "HAMZA_ON_YA_AS_YA": "Possible historical form: yāʾ written without the hamza seat (ي for ئ).",
    "HAMZA_ON_WAW_AS_WAW": "Possible historical form: wāw written without hamza (و for ؤ).",
    "INITIAL_HAMZA_OMITTED": "Possible historical form: initial hamza omitted or its seat neutralized.",
    "ALIF_MAQSURA_AS_YA": "Possible historical form: yāʾ used for alif maqṣūra (ي for ى).",
    "FINAL_HAMZA_OMITTED": "Possible historical form: final hamza after alif omitted.",
    "ONE_ALIF_VARIANT": "Possible historical form: one internal alif inserted or omitted.",
    "FINAL_ALIF_AFTER_WAW": "Possible historical form: differentiating alif after plural wāw omitted (و for وا).",
    "LEGACY_MAP": "Possible historical/legacy spelling recorded in external Ortografia.adz.",
}


@dataclass
class Suggestion:
    target: str
    kind: str  # historical | correction
    confidence: float
    score: float
    annotation: str
    evidence: str

    def to_dict(self):
        return asdict(self)


@dataclass
class TokenResult:
    index: int
    surface: str
    start: int
    end: int
    status: str  # recognized | historical | correction | unresolved
    annotation: str
    suggestions: list[Suggestion]
    lexical_candidates: int = 0

    def to_dict(self):
        d = asdict(self)
        d["suggestions"] = [s.to_dict() for s in self.suggestions]
        return d


def _same_shape(a: str, b: str) -> bool:
    return any(a in g and b in g for g in SHAPE_GROUPS)


def weighted_edit_distance(a: str, b: str) -> float:
    """Small weighted Damerau-Levenshtein distance for Arabic OCR/HTR ranking."""
    a, b = strip_diacritics(a), strip_diacritics(b)
    if a == b:
        return 0.0
    prev = [float(j) for j in range(len(b) + 1)]
    prevprev = None
    for i, ca in enumerate(a, 1):
        cur = [float(i)] + [0.0] * len(b)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                sub = 0.0
            elif _same_shape(ca, cb):
                sub = 0.42
            elif {ca, cb} <= set("اأإآٱ"):
                sub = 0.35
            elif {ca, cb} <= set("ةه"):
                sub = 0.55
            elif {ca, cb} <= set("يىئ"):
                sub = 0.55
            elif {ca, cb} <= set("وؤ"):
                sub = 0.55
            else:
                sub = 1.0
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0, prev[j - 1] + sub)
            if prevprev is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prevprev[j - 2] + 0.72)
        prevprev, prev = prev, cur
    d = prev[-1]
    # Adjacent duplication is especially common in OCR/HTR and typing.
    if b in single_redundant_letter_variants(a) or a in single_redundant_letter_variants(b):
        d = min(d, 0.35)
    return d


class CorrectionEngine:
    """lexicon-backed spelling correction layer for OCR/HTR text.

    The configured lexical resources remain authoritative for lexical/morphological validation. This class
    adds spelling suggestions and keeps historical orthography separate from
    probable OCR/HTR mistakes. No source text is rewritten automatically.
    """

    def __init__(self, resource_root: Path):
        self.resource_root = Path(resource_root)
        self.analyzer = NativeAnalyzer(self.resource_root)
        self.valid_direct = set(self.analyzer.lexicon.exact) | set(self.analyzer.morph.inflect) | set(self.analyzer.morph.verb) | set(self.analyzer.training.exact) | set(self.analyzer.training.stems)
        self.form_frequency: Counter[str] = Counter()
        self.training_hist_surfaces: dict[str, Counter[str]] = defaultdict(Counter)
        self._load_training_surface_counts()
        self.fuzzy_vocab = sorted(set(self.analyzer.lexicon.exact) | set(self.analyzer.training.exact) | set(self.analyzer.training.stems))
        self.fuzzy_by_len: dict[int, list[str]] = defaultdict(list)
        for w in self.fuzzy_vocab:
            if w:
                self.fuzzy_by_len[len(w)].append(w)
        self._valid_cache: dict[str, tuple[bool, int, float, str]] = {}
        # Canonical written surfaces.  This is intentionally stricter than
        # _validate(): a historical spelling can sometimes receive an accidental
        # alternative clitic analysis, but that does not make the spelling itself
        # a standard dictionary form.
        self.standard_written_forms = (
            set(self.analyzer.lexicon.exact)
            | set(self.analyzer.morph.inflect)
            | set(self.analyzer.morph.verb)
            | set(self.analyzer.training.stems)
        )
        self.legacy_reverse: dict[str, set[str]] = defaultdict(set)
        for historical_surface, canonical_forms in self.analyzer.legacy_orthography.map.items():
            for canonical in canonical_forms:
                self.legacy_reverse[strip_diacritics(canonical)].add(strip_diacritics(historical_surface))
        self._token_cache: dict[tuple[str, str, int], tuple[str, str, list[Suggestion], int]] = {}

    def _load_training_surface_counts(self):
        p = self.resource_root / "controlled_corpus" / "controlled_corpus.jsonl"
        excluded = {"Abou_Qurra", "GRNA_Or_43_ARA", "GRNA Or. 43", "GRNA"}
        if not p.exists():
            return
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if (r.get("work") or "").strip() in excluded:
                    continue
                s = strip_diacritics((r.get("normalized_surface") or r.get("surface") or "").strip())
                if s:
                    self.form_frequency[s] += 1
                    self.training_hist_surfaces[historical_key(s)][s] += 1

    @staticmethod
    def tokenize(text: str):
        out = []
        for m in ARABIC_TOKEN_RE.finditer(text or ""):
            surface = m.group(0)
            if any(unicodedata.category(ch).startswith("L") for ch in surface):
                out.append((surface, m.start(), m.end()))
        return out

    def _validate(self, form: str) -> tuple[bool, int, float, str]:
        """Validate a proposed written form through the configured lexical resources in non-historical mode."""
        form = strip_diacritics(form)
        cached = self._valid_cache.get(form)
        if cached is not None:
            return cached
        cs = self.analyzer.training.candidates(form, False) + self.analyzer.training.stem_candidates(form) + self.analyzer.morph.analyze_with_clitics(form, False)
        best = {}
        for c in cs:
            if c.key not in best or c.score > best[c.key].score:
                best[c.key] = c
        cs = list(best.values())
        if not cs:
            ans = (False, 0, 0.0, "")
        else:
            top = max(cs, key=lambda c: c.score)
            ev = "; ".join(dict.fromkeys(e.source for e in top.evidence))
            ans = (True, len(cs), float(top.score), ev)
        self._valid_cache[form] = ans
        return ans

    def _is_exactly_recognized(self, surface: str) -> tuple[bool, int, float, str]:
        return self._validate(strip_diacritics(surface))

    def _is_standard_written_form(self, surface: str) -> bool:
        """True only for a canonical lexical/morphological resources surface.

        Attached clitic prefixes and pronominal suffixes are allowed when the
        lexical base is canonical.  However, a final -ه is not accepted merely
        as a pronoun suffix when the same written token has a canonical -ة
        counterpart (e.g. دراسه → دراسة).  That ambiguity must stay available
        to the historical-form detector.
        """
        s = strip_diacritics(surface)

        def min_prefix_depth(form: str) -> int | None:
            """Minimum detachable-prefix count needed to reach a canonical base."""
            if form in self.standard_written_forms:
                # A stem recovered only from the controlled corpus is useful
                # evidence, but it is not automatically a canonical spelling.
                # If the same conservative historical key points to a distinct
                # canonical lexicon/morphology form (e.g. سوال -> سؤال), keep
                # the recovered stem historical rather than promoting it to
                # standard. Explicit lexicon/overlay forms such as عطايا still
                # remain canonical.
                direct_canonical = (
                    form in self.analyzer.lexicon.exact
                    or form in self.analyzer.morph.inflect
                    or form in self.analyzer.morph.verb
                )
                if not direct_canonical and form in self.analyzer.training.stems:
                    hk = historical_key(form)
                    competing = [
                        written for _entry, written
                        in self.analyzer.lexicon.historical.get(hk, ())
                        if written != form
                    ]
                    if not competing:
                        return 0
                else:
                    return 0
            depths=[]
            for chain,base in self.analyzer.morph._prefix_paths(form,3):
                if base not in self.standard_written_forms:
                    continue
                # Definite article chains cannot make a verb into a canonical
                # written word merely because the verb base exists.
                if any(p in {'ال','وال','فال','بال','كال','لل'} for p in chain):
                    base_cs=(self.analyzer.training.stem_candidates(base)+self.analyzer.morph.base_candidates(base,False))
                    if not any(c.broad_pos != 'VERB' for c in base_cs):
                        continue
                depths.append(len(chain))
            return min(depths) if depths else None

        def canonical_without_suffix(form: str) -> bool:
            return min_prefix_depth(form) is not None

        def suffix_base_depth(written_base: str) -> int | None:
            """Canonical depth for a stem immediately before a pronoun suffix.

            Includes documented written stem alternations such as ة→ت, ى→ا,
            and final hamza seat alternation (ء→ئ/ؤ before a suffix).
            """
            depths = []
            for base in self.analyzer.morph._suffix_base_variants(written_base):
                d = min_prefix_depth(base)
                if d is not None:
                    depths.append(d)
            return min(depths) if depths else None

        # Direct dictionary/morphology spellings always win.
        if canonical_without_suffix(s):
            return True

        # A final ه is intrinsically ambiguous: it can be the 3ms pronominal
        # suffix (نفسه = نفس + ه) or a historical graphic spelling of ة
        # (دراسه -> دراسة).  Before proposing ه->ة, give precedence to an
        # *attested pronominal analysis* in the controlled controlled corpus.
        #
        # Prefixes must be transparent to this test.  If تلميذه is attested as
        # تلميذ + ه, then وتلميذه, فتلميذه, بتلميذه, ... inherit that evidence
        # after peeling the detachable prefix chain.  This avoids a false
        # وتلميذه -> وتلميذة normalization merely because تلميذة also exists.
        # The rule remains conservative: دراسه is not protected simply because
        # دراس + ه is mechanically analyzable; it needs an attested suffix form.
        def attested_pronominal(form: str) -> bool:
            for c in self.analyzer.training.candidates(form, False):
                if "@DMT" in (c.pos or "") and any(
                    ev.source == "controlled_corpus_exact_surface" for ev in c.evidence
                ):
                    return True
            return False

        if s.endswith("ه"):
            base_with_prefix = s[:-1]
            pron_depth = suffix_base_depth(base_with_prefix)
            ta_depth = min_prefix_depth(base_with_prefix + "ة")

            # Structural competition: prefer the reading that requires fewer
            # detachable-prefix assumptions.  فرائضه is directly فرائض + ه
            # (depth 0), while the competing فرائضة is only analyzable as
            # ف + رائضة (depth 1), so the pronominal reading wins.  For
            # دراسه / دراسة both readings are depth 0, so historical review
            # remains available rather than being suppressed mechanically.
            if pron_depth is not None and (ta_depth is None or pron_depth < ta_depth):
                return True

            # Exact full written form (e.g. نفسه, تلميذه).
            if attested_pronominal(s) and pron_depth is not None:
                return True
            # Prefix-transparent inheritance (e.g. و + تلميذ + ه).  Peel only
            # recognized clitic prefixes from the lexical base, then test
            # the unprefixed base+ه form against exact controlled evidence.
            for chain, base in self.analyzer.morph._prefix_paths(base_with_prefix, 3):
                if not chain or suffix_base_depth(base) is None:
                    continue
                if attested_pronominal(base + "ه"):
                    return True

        # Keep historical hāʾ-for-tāʾ-marbūṭa detection alive only when the
        # exact written token lacks stronger full-surface pronoun evidence.
        # This preserves دراسه -> دراسة while suppressing false نفسة for نفسه.
        if s.endswith("ه") and canonical_without_suffix(s[:-1] + "ة"):
            return False

        # Canonical pronominal suffixes: سؤالهم = سؤال + هم, سؤاله = سؤال + ه.
        # The earlier v0.2.1 filter handled prefixes only, causing valid suffixed
        # targets such as سؤالهم to be discarded after source lexical system had validated them.
        for suf in DMT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                base = s[:-len(suf)]
                if suffix_base_depth(base) is not None:
                    return True
        return False

    def _reverse_historical_variants(self, canonical: str, max_depth: int = 2) -> list[tuple[str, str]]:
        """Generate conservative historical spellings of a standard lexically validated form.

        These are used only in the *preserve historical spelling* workflow,
        after ``canonical`` has already been validated by source lexical system.  The method is
        deliberately finite and auditable; it does not invent arbitrary fuzzy
        spellings.
        """
        canonical = strip_diacritics(canonical)
        if not canonical:
            return []

        # Explicit reverse mappings from the legacy source lexical system orthography dictionary.
        results: dict[str, tuple[str, ...]] = {}
        for hist in self.legacy_reverse.get(canonical, ()):
            if hist and hist != canonical:
                results[hist] = (HISTORICAL_LABELS["LEGACY_MAP"],)

        def one_step(form: str):
            rows = []
            if form.endswith("ة"):
                rows.append((form[:-1] + "ه", HISTORICAL_LABELS["TA_MARBUTA_AS_HA"]))
            if form.endswith("ى"):
                rows.append((form[:-1] + "ي", HISTORICAL_LABELS["ALIF_MAQSURA_AS_YA"]))
            if form and form[0] in HAMZA_ALIF - {"ا"}:
                rows.append(("ا" + form[1:], HISTORICAL_LABELS["INITIAL_HAMZA_OMITTED"]))
            if form.endswith("اء"):
                rows.append((form[:-1], HISTORICAL_LABELS["FINAL_HAMZA_OMITTED"]))
            if form.endswith("وا") and len(form) >= 3:
                rows.append((form[:-1], HISTORICAL_LABELS["FINAL_ALIF_AFTER_WAW"]))
            for i, ch in enumerate(form):
                if ch == "ئ":
                    rows.append((form[:i] + "ي" + form[i + 1 :], HISTORICAL_LABELS["HAMZA_ON_YA_AS_YA"]))
                elif ch == "ؤ":
                    rows.append((form[:i] + "و" + form[i + 1 :], HISTORICAL_LABELS["HAMZA_ON_WAW_AS_WAW"]))
            # A conservative reverse of the one-alif rule: omission only.
            for i in range(1, len(form) - 1):
                if form[i] == "ا":
                    rows.append((form[:i] + form[i + 1 :], HISTORICAL_LABELS["ONE_ALIF_VARIANT"]))
            return rows

        frontier = {canonical: tuple()}
        seen = {canonical}
        for _depth in range(max_depth):
            nxt = {}
            for form, labels in frontier.items():
                for hist, label in one_step(form):
                    if not hist or hist == canonical:
                        continue
                    new_labels = tuple(dict.fromkeys(labels + (label,)))
                    old = results.get(hist)
                    if old is None or len(new_labels) < len(old):
                        results[hist] = new_labels
                    if hist not in seen:
                        seen.add(hist)
                        nxt[hist] = new_labels
            frontier = nxt
            if not frontier:
                break

        out = [(form, " ".join(labels)) for form, labels in results.items()]
        out.sort(key=lambda row: (weighted_edit_distance(canonical, row[0]), row[0]))
        return out

    def _historical_alternatives_for_standard(self, canonical: str, max_results: int = 5) -> list[Suggestion]:
        """Return licensed historical spellings for an already-standard form.

        Preserve mode uses these as *optional editorial alternatives*, not as
        corrections and not as review flags.  The canonical form has already
        been validated before this method is called.
        """
        canonical = strip_diacritics(canonical)
        rows: list[Suggestion] = []
        for hist, label in self._reverse_historical_variants(canonical):
            # The generic one-alif rule is useful as a detector when such a
            # spelling is actually encountered, but it is too permissive to
            # manufacture hypothetical variants from every standard word.
            # Keep it out of the informational reverse list unless an explicit
            # legacy mapping has licensed the form.
            if HISTORICAL_LABELS["ONE_ALIF_VARIANT"] in label and HISTORICAL_LABELS["LEGACY_MAP"] not in label:
                continue
            d = weighted_edit_distance(canonical, hist)
            if d > 2.20:
                continue
            if d <= 0.55:
                conf = 0.96
            elif d <= 1.10:
                conf = 0.93
            else:
                conf = 0.88
            score = 100.0 * conf - 3.0 * d
            rows.append(Suggestion(
                hist,
                "historical_alternative",
                conf,
                score,
                "Historical spelling alternative to the standard form. " + label,
                f"standard counterpart: {canonical}; licensed historical orthographic rule",
            ))
        rows.sort(key=lambda x: (-x.score, -x.confidence, x.target))
        seen = set()
        out = []
        for row in rows:
            if row.target in seen:
                continue
            seen.add(row.target)
            out.append(row)
            if len(out) >= max_results:
                break
        return out

    def _preserve_mode_targets(self, source: str, standard: list[Suggestion], max_results: int = 5) -> list[Suggestion]:
        """Put corrections that retain licensed historical spelling first."""
        s = strip_diacritics(source)
        historical_rows: dict[str, Suggestion] = {}
        for base in standard:
            base_distance = weighted_edit_distance(s, base.target)
            for hist, label in self._reverse_historical_variants(base.target):
                if hist == s:
                    continue
                d = weighted_edit_distance(s, hist)
                # The historical spelling must itself be a plausible reading of
                # the OCR/HTR token, not just a remote variant of a candidate.
                if d > 2.15 or d > base_distance + 0.80:
                    continue
                if d <= 0.50:
                    conf = max(0.88, min(0.97, base.confidence + 0.04))
                elif d <= 1.05:
                    conf = max(0.80, min(0.93, base.confidence))
                else:
                    conf = max(0.64, min(0.84, base.confidence - 0.04))
                score = base.score + 7.0 - 6.0 * d
                ann = "Possible OCR/HTR correction preserving historical spelling. " + label
                ev = f"lexically validated standard counterpart: {base.target}; {base.evidence}"
                cand = Suggestion(hist, "historical_correction", conf, score, ann, ev)
                old = historical_rows.get(hist)
                if old is None or cand.score > old.score:
                    historical_rows[hist] = cand

        hist = sorted(historical_rows.values(), key=lambda x: (-x.score, -x.confidence, x.target))
        # In preserve mode historical-spelling corrections are intentionally
        # displayed before standardizing corrections, regardless of raw score.
        combined = hist + standard
        seen = set()
        out = []
        for cand in combined:
            if cand.target in seen:
                continue
            seen.add(cand.target)
            out.append(cand)
            if len(out) >= max_results:
                break
        return out

    def _historical_relation(self, source: str, target: str) -> tuple[bool, str]:
        s, t = strip_diacritics(source), strip_diacritics(target)
        if s == t:
            return False, ""
        labels = []
        # Explicit legacy dictionary is strongest.
        if t in self.analyzer.legacy_orthography.canonical_forms(s):
            labels.append(HISTORICAL_LABELS["LEGACY_MAP"])
        if t.endswith("ة") and s.endswith("ه") and s[:-1] == t[:-1]:
            labels.append(HISTORICAL_LABELS["TA_MARBUTA_AS_HA"])
        if len(s) == len(t):
            changed = [(a, b) for a, b in zip(s, t) if a != b]
            if changed and all(a == "ي" and b == "ئ" for a, b in changed):
                labels.append(HISTORICAL_LABELS["HAMZA_ON_YA_AS_YA"])
            if changed and all(a == "و" and b == "ؤ" for a, b in changed):
                labels.append(HISTORICAL_LABELS["HAMZA_ON_WAW_AS_WAW"])
            if s[1:] == t[1:] and s[0] == "ا" and t[0] in HAMZA_ALIF - {"ا"}:
                labels.append(HISTORICAL_LABELS["INITIAL_HAMZA_OMITTED"])
        if s.endswith("ا") and t.endswith("اء") and s[:-1] == t[:-2]:
            labels.append(HISTORICAL_LABELS["FINAL_HAMZA_OMITTED"])
        if s.endswith("و") and t == s + "ا":
            labels.append(HISTORICAL_LABELS["FINAL_ALIF_AFTER_WAW"])
        if is_one_alif_variant(s, t):
            labels.append(HISTORICAL_LABELS["ONE_ALIF_VARIANT"])
        return bool(labels), " ".join(dict.fromkeys(labels))

    def _historical_targets(self, surface: str, max_results: int = 6) -> list[Suggestion]:
        s = strip_diacritics(surface)
        raw: dict[str, tuple[float, str, str]] = {}

        def add(target: str, confidence: float, note: str, evidence: str):
            target = strip_diacritics(target)
            if not target or target == s:
                return
            ok, _, _, lex_ev = self._validate(target)
            if not ok:
                return
            old = raw.get(target)
            record = (confidence, note, evidence + (f"; {lex_ev}" if lex_ev else ""))
            if old is None or confidence > old[0]:
                raw[target] = record

        # Original external Ortografia.adz mappings.
        for t in self.analyzer.legacy_orthography.canonical_forms(s):
            add(t, 0.98, HISTORICAL_LABELS["LEGACY_MAP"], "external Ortografia.adz")

        # Corpus forms that collapse to the same documented historical key.
        for t, n in self.training_hist_surfaces.get(historical_key(s), Counter()).most_common(8):
            hist, label = self._historical_relation(s, t)
            if hist:
                add(t, min(0.97, 0.90 + 0.01 * min(n, 7)), label, f"controlled controlled corpus; occurrences={n}")

        # Direct lexicon/morphology matches under the conservative historical
        # key can encode more than one graphic relation at once (e.g. اعدا → أعداء).
        hk = historical_key(s)
        indexed = []
        indexed.extend((form, "lexicon historical index") for _e, form in self.analyzer.lexicon.historical.get(hk, ()))
        indexed.extend((form, "morphology historical index") for _e, form, _kind in self.analyzer.morph.historical_inflect.get(hk, ()))
        seen_indexed = set()
        for t, ev in indexed:
            t = strip_diacritics(t)
            if not t or t == s or t in seen_indexed:
                continue
            seen_indexed.add(t)
            codes = [code for code in explain_match(s, t) if code != "ALIF_MAQSURA_AS_YA"]
            labels = [HISTORICAL_LABELS.get(code) for code in codes if HISTORICAL_LABELS.get(code)]
            if labels:
                add(t, 0.94 if len(labels) > 1 else 0.92, " ".join(dict.fromkeys(labels)), ev)

        # Reversible historical graphics, validated against source lexical system.
        if s.endswith("ه"):
            t = s[:-1] + "ة"
            hist, label = self._historical_relation(s, t)
            if hist:
                add(t, 0.95, label, "historical rule + valid lexical analysis")
        for i, ch in enumerate(s):
            if ch == "ي":
                t = s[:i] + "ئ" + s[i + 1 :]
                hist, label = self._historical_relation(s, t)
                if hist:
                    add(t, 0.91, label, "historical rule + valid lexical analysis")
            elif ch == "و":
                t = s[:i] + "ؤ" + s[i + 1 :]
                hist, label = self._historical_relation(s, t)
                if hist:
                    add(t, 0.91, label, "historical rule + valid lexical analysis")
        # Bare alif for a hamzated lexical stem, including after common clitics
        # or the definite article. Do not turn the article's own alif into hamza.
        hamza_bases = []
        if not s.startswith("ال"):
            hamza_bases.append(("", s))
        hamza_bases.extend(("".join(chain), base) for chain, base in self.analyzer.morph._prefix_paths(s, 3))
        seen_hamza = set()
        for pref, base in hamza_bases:
            if not (base.startswith("ا") and len(base) >= 3):
                continue
            for h in ("أ", "إ", "آ"):
                t = pref + h + base[1:]
                if t in seen_hamza:
                    continue
                seen_hamza.add(t)
                ok, _, _, lex_ev = self._validate(t)
                if ok:
                    add(t, 0.90, HISTORICAL_LABELS["INITIAL_HAMZA_OMITTED"], "historical hamza rule" + (f"; {lex_ev}" if lex_ev else ""))
        if s.endswith("ا") and len(s) >= 3:
            t = s + "ء"
            hist, label = self._historical_relation(s, t)
            if hist:
                add(t, 0.88, label, "historical rule + valid lexical analysis")
        if s.endswith("و") and len(s) >= 3:
            t = s + "ا"
            hist, label = self._historical_relation(s, t)
            if hist:
                add(t, 0.93, label, "historical rule + valid lexical analysis")

        # One internal alif inserted/omitted, only when the result has an lexical analysis.
        for i in range(1, len(s)):
            if s[i] == "ا" and i < len(s) - 1:
                t = s[:i] + s[i + 1 :]
                hist, label = self._historical_relation(s, t)
                if hist:
                    add(t, 0.82, label, "historical one-alif rule")
        for i in range(1, len(s)):
            t = s[:i] + "ا" + s[i:]
            hist, label = self._historical_relation(s, t)
            if hist:
                add(t, 0.80, label, "historical one-alif rule")

        # If the historical morphology itself recovered a valid lexical form,
        # retain its documented rule as evidence; duplication is deliberately
        # excluded here and treated as probable OCR/HTR error below.
        hist_cs = self.analyzer.morph.analyze_with_clitics(s, True)
        has_real_historical = any(
            c.historical_rules and not all("duplicated letter" in r for r in c.historical_rules)
            for c in hist_cs
        )
        if not has_real_historical and not raw:
            return []

        out = []
        for t, (conf, note, ev) in raw.items():
            dist = weighted_edit_distance(s, t)
            score = 100.0 * conf - 3.0 * dist + min(4.0, math.log1p(self.form_frequency.get(t, 0)))
            out.append(Suggestion(t, "historical", conf, score, note, ev))
        out.sort(key=lambda x: (-x.score, x.target))
        # When a strong documented normalization exists, suppress much weaker
        # one-alif alternatives that would clutter the editorial review pane.
        if out and out[0].confidence >= 0.90:
            out = [x for x in out if x.confidence >= 0.86]
        return out[:max_results]

    def _one_edit_valid(self, source: str, limit: int = 50) -> set[str]:
        """Generate one-edit forms and keep only lexically validated direct written forms."""
        s = strip_diacritics(source)
        found = set()
        # Deletion and transposition.
        for i in range(len(s)):
            cand = s[:i] + s[i + 1 :]
            if cand in self.valid_direct:
                found.add(cand)
        for i in range(len(s) - 1):
            if s[i] != s[i + 1]:
                cand = s[:i] + s[i + 1] + s[i] + s[i + 2 :]
                if cand in self.valid_direct:
                    found.add(cand)
        # Substitution and insertion. Dictionary membership makes this bounded.
        for i in range(len(s)):
            for ch in ARABIC_ALPHABET:
                if ch == s[i]:
                    continue
                cand = s[:i] + ch + s[i + 1 :]
                if cand in self.valid_direct:
                    found.add(cand)
                    if len(found) >= limit * 3:
                        break
        for i in range(len(s) + 1):
            for ch in ARABIC_ALPHABET:
                cand = s[:i] + ch + s[i:]
                if cand in self.valid_direct:
                    found.add(cand)
                    if len(found) >= limit * 3:
                        break
        return found

    def _correct_component(self, component: str, max_results: int = 30) -> list[tuple[str, float, str]]:
        s = strip_diacritics(component)
        candidates = self._one_edit_valid(s, max_results)
        rows = []
        for t in candidates:
            hist, _ = self._historical_relation(s, t)
            if hist:
                continue
            d = weighted_edit_distance(s, t)
            freq = self.form_frequency.get(t, 0)
            score = 92.0 - 28.0 * d + min(8.0, 1.5 * math.log1p(freq))
            rows.append((t, score, f"lexically validated form; weighted edit distance={d:.2f}; corpus frequency={freq}"))
        rows.sort(key=lambda x: (-x[1], x[0]))
        return rows[:max_results]

    def _fuzzy_fallback(self, source: str, max_results: int = 8) -> list[tuple[str, float, str]]:
        if process is None or fuzz is None:
            return []
        s = strip_diacritics(source)
        pool = []
        for ln in range(max(2, len(s) - 2), len(s) + 3):
            pool.extend(self.fuzzy_by_len.get(ln, ()))
        if not pool:
            return []
        hits = process.extract(s, pool, scorer=fuzz.ratio, limit=max(20, max_results * 4), score_cutoff=58)
        out = []
        for t, ratio, _ in hits:
            if t == s:
                continue
            hist, _ = self._historical_relation(s, t)
            if hist:
                continue
            d = weighted_edit_distance(s, t)
            if d > 2.15:
                continue
            freq = self.form_frequency.get(t, 0)
            score = 68.0 + 0.15 * ratio - 11.0 * d + min(6.0, math.log1p(freq))
            out.append((t, score, f"near dictionary/corpus form; weighted edit distance={d:.2f}; similarity={ratio:.0f}%"))
        out.sort(key=lambda x: (-x[1], x[0]))
        return out[:max_results]

    def _ocr_targets(self, surface: str, max_results: int = 5) -> list[Suggestion]:
        s = strip_diacritics(surface)
        proposals: dict[str, tuple[float, str]] = {}

        def record(target: str, score: float, evidence: str):
            if not target or target == s:
                return
            target = strip_diacritics(target)

            # An OCR operation can first recover a historically spelled form
            # (e.g. الكننيسه → الكنيسه).  In the standard correction pool,
            # continue through the historical layer to its canonical lexically validated form
            # (الكنيسة). Preserve mode can later derive and rank الكنيسه first.
            if not self._is_standard_written_form(target):
                hs = self._historical_targets(target, 4)
                canonical = next(
                    (h for h in hs if h.confidence >= 0.88 and self._is_standard_written_form(h.target)),
                    None,
                )
                if canonical is not None:
                    evidence += f"; intermediate historical spelling {target} → standard {canonical.target}"
                    target = canonical.target

            # The standard correction pool is used by Standardize mode.  Never
            # leak a historical/non-canonical intermediate spelling into it.
            # Preserve mode derives historical spellings separately from the
            # canonical targets in _preserve_mode_targets().
            if not self._is_standard_written_form(target):
                return

            hist, _ = self._historical_relation(s, target)
            # A direct source→target historical relation belongs to the
            # historical detector rather than the OCR error pool.
            if hist:
                return
            prev = proposals.get(target)
            if prev is None or score > prev[0]:
                proposals[target] = (score, evidence)

        # Full written token.
        for t, score, ev in self._correct_component(s):
            record(t, score, ev)

        # Preserve recognized clitic prefixes while correcting the lexical base.
        for chain, base in self.analyzer.morph._prefix_paths(s, 3):
            prefix = "".join(chain)
            for t, score, ev in self._correct_component(base, 15):
                full = prefix + t
                ok, _, _, lex_ev = self._validate(full)
                if ok:
                    record(full, score + 3.0, f"clitic-preserving correction ({'+'.join(chain)} + {t}); {lex_ev}; {ev}")

        # Preserve an attached pronominal suffix; also permit prefix + suffix.
        for suf in DMT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                base = s[: -len(suf)]
                for t, score, ev in self._correct_component(base, 15):
                    full = t + suf
                    ok, _, _, lex_ev = self._validate(full)
                    if ok:
                        record(full, score + 2.0, f"suffix-preserving correction ({t} + {suf}); {lex_ev}; {ev}")
                for chain, stem in self.analyzer.morph._prefix_paths(base, 3):
                    prefix = "".join(chain)
                    for t, score, ev in self._correct_component(stem, 10):
                        full = prefix + t + suf
                        ok, _, _, lex_ev = self._validate(full)
                        if ok:
                            record(full, score + 3.0, f"clitic+suffix correction; {lex_ev}; {ev}")
                break

        # Strong dedicated duplicate-letter correction: not classified as historical.
        for t in single_redundant_letter_variants(s):
            ok, _, _, lex_ev = self._validate(t)
            if ok:
                record(t, 99.0, f"probable OCR/HTR duplicated character; {lex_ev}")

        if not proposals:
            for t, score, ev in self._fuzzy_fallback(s):
                record(t, score, ev)

        out = []
        for t, (score, ev) in proposals.items():
            d = weighted_edit_distance(s, t)
            if "duplicated character" in ev:
                conf = 0.96
                ann = "Probable OCR/HTR error: duplicated adjacent character."
            elif d <= 0.5:
                conf = 0.91
                ann = "Probable OCR/HTR character-shape confusion."
            elif d <= 1.05:
                conf = 0.82
                ann = "Possible OCR/HTR error: one-character correction supported by the lexical resources."
            else:
                conf = 0.66
                ann = "Possible OCR/HTR error: near dictionary/corpus form; review recommended."
            out.append(Suggestion(t, "correction", conf, score, ann, ev))
        out.sort(key=lambda x: (-x.score, -x.confidence, x.target))
        if any(x.confidence >= 0.95 and "duplicated" in x.annotation for x in out):
            out = [x for x in out if x.confidence >= 0.95 and "duplicated" in x.annotation]
        return out[:max_results]

    def analyze_token(
        self,
        index: int,
        surface: str,
        start: int,
        end: int,
        historical: bool = True,
        max_suggestions: int = 5,
        editorial_mode: str = "standardize",
    ) -> TokenResult:
        s = strip_diacritics(surface)
        editorial_mode = "preserve" if editorial_mode == "preserve" else "standardize"
        key = (s, editorial_mode if historical else "historical-off", int(max_suggestions))
        cached = self._token_cache.get(key)
        if cached is not None:
            status, annotation, suggestions, ncs = cached
            return TokenResult(index, surface, start, end, status, annotation, list(suggestions), ncs)

        exact, ncs, _, _ = self._is_exactly_recognized(s)

        # A canonical token is already correct.  In Standardize mode it needs
        # no alternative.  In Preserve mode, however, the editor may want to
        # know which documented historical spellings correspond to this standard
        # form.  Those alternatives are editorial options. In Preserve mode, a standard
        # token with at least one licensed historical alternative is a real
        # editorial choice and is therefore included in the Review queue by the UI.
        if self._is_standard_written_form(s):
            if editorial_mode == "preserve" and historical:
                alts = self._historical_alternatives_for_standard(s, max_suggestions)
                if alts:
                    note = (
                        "Standard form. This spelling is standard, not historical. "
                        "Preserve-historical mode shows licensed historical spellings below as optional alternatives. Because a historical alternative exists, this standard form is included in the Review queue as an editorial choice."
                    )
                else:
                    note = "Standard form. This spelling is standard, not historical; no licensed historical alternative was generated."
                self._token_cache[key] = ("recognized", note, list(alts), ncs)
                return TokenResult(index, surface, start, end, "recognized", note, alts, ncs)
            note = "Standard form recognized directly in the lexical/morphological resources; no spelling replacement is needed."
            self._token_cache[key] = ("recognized", note, [], ncs)
            return TokenResult(index, surface, start, end, "recognized", note, [], ncs)

        if historical:
            hs = self._historical_targets(s, max_suggestions * 2 if editorial_mode == "standardize" else max_suggestions)
            if editorial_mode == "standardize":
                # Historical rules are DETECTORS in standardization mode, not
                # generators of historical spelling alternatives.  Keep only
                # canonical standard written forms as replacement targets.
                hs = [h for h in hs if self._is_standard_written_form(h.target)][:max_suggestions]
            if hs and (not exact or hs[0].confidence >= 0.90):
                if editorial_mode == "standardize":
                    note = hs[0].annotation + " Standardization mode: only canonical standard written forms are offered as replacements."
                    if exact:
                        note += " The lexical resources also have an alternative exact analysis of the written form, so no change is automatic."
                    self._token_cache[key] = ("historical", note, list(hs), ncs)
                    return TokenResult(index, surface, start, end, "historical", note, hs, ncs)

                # Preserve mode: a licensed historical spelling is already an
                # acceptable reading.  Do not ask the editor to normalize it and
                # do not put it in the Review queue.  Standard counterparts are
                # shown only as reference information.
                refs = []
                for h in hs:
                    if not self._is_standard_written_form(h.target):
                        continue
                    refs.append(Suggestion(
                        h.target,
                        "standard_reference",
                        h.confidence,
                        h.score,
                        "Standard counterpart (reference only). " + h.annotation,
                        h.evidence,
                    ))
                    if len(refs) >= max_suggestions:
                        break
                note = (
                    hs[0].annotation
                    + " Historical spelling recognized and preserved in this editorial mode; no correction or normalization review is required."
                )
                self._token_cache[key] = ("historical", note, list(refs), ncs)
                return TokenResult(index, surface, start, end, "historical", note, refs, ncs)

        if exact:
            note = "Recognized by the lexical resources; no spelling correction proposed."
            self._token_cache[key] = ("recognized", note, [], ncs)
            return TokenResult(index, surface, start, end, "recognized", note, [], ncs)

        standard = self._ocr_targets(s, max(max_suggestions * 2, 8))
        if standard:
            if editorial_mode == "preserve":
                cs = self._preserve_mode_targets(s, standard, max_suggestions)
                note = cs[0].annotation
                if cs and cs[0].kind == "historical_correction":
                    note += " Historical-spelling corrections are ranked first because preserve-historical mode is active."
            else:
                cs = standard[:max_suggestions]
                note = cs[0].annotation + " Standardization mode prefers the standard standard written form."
            self._token_cache[key] = ("correction", note, list(cs), 0)
            return TokenResult(index, surface, start, end, "correction", note, cs, 0)

        note = "Not recognized by the lexical resources and no sufficiently close lexical correction was found."
        self._token_cache[key] = ("unresolved", note, [], 0)
        return TokenResult(index, surface, start, end, "unresolved", note, [], 0)

    def analyze(
        self,
        text: str,
        historical: bool = True,
        max_suggestions: int = 5,
        editorial_mode: str = "standardize",
    ):
        editorial_mode = "preserve" if editorial_mode == "preserve" else "standardize"
        toks = self.tokenize(text)
        results = [
            self.analyze_token(i + 1, surface, start, end, historical, max_suggestions, editorial_mode)
            for i, (surface, start, end) in enumerate(toks)
        ]
        counts = Counter(r.status for r in results)
        return {
            "text": text,
            "editorial_mode": editorial_mode,
            "historical_detection": bool(historical),
            "tokens": [r.to_dict() for r in results],
            "summary": {
                "tokens": len(results),
                "recognized": counts.get("recognized", 0),
                "historical": counts.get("historical", 0),
                "correction": counts.get("correction", 0),
                "unresolved": counts.get("unresolved", 0),
            },
            "rapidfuzz": process is not None,
            "lexicon_entries": len(self.analyzer.lexicon.entries),
            "controlled_corpus_tokens": self.analyzer.training.counts,
        }
