from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass(frozen=True)
class Evidence:
    source: str
    detail: str = ""
    weight: float = 0.0

@dataclass
class Candidate:
    lemma: str
    pos: str
    root: str = ""
    secondary: str = ""
    broad_pos: str = "OTHER"
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    historical_rules: list[str] = field(default_factory=list)
    rank_support: float = 0.0
    ai_score: float | None = None
    ai_explanation: str = ""
    correction_support: float = 0.0
    correction_explanation: str = ""

    @property
    def key(self) -> tuple[str,str,str]:
        return (self.lemma, self.pos, self.root)

    def to_dict(self) -> dict[str,Any]:
        d=asdict(self)
        d["evidence"]=[asdict(x) for x in self.evidence]
        return d

@dataclass
class TokenAnalysis:
    index: int
    surface: str
    candidates: list[Candidate]
    selected: int | None = None
    mode: str = "native-alpha"
    decision_reason: str = ""
    lexical_support: float = 0.0
    decision_margin: float = 0.0
    review_reason: str = ""
    review_priority: str = "none"
    page: str = ""
    paragraph: str = ""
    line: str = ""
    source_line: int = 0
    word_in_paragraph: int = 0
    legacy_location: str = "0:0:0"

    @property
    def selected_candidate(self) -> Candidate | None:
        if self.selected is None or not self.candidates: return None
        if 0 <= self.selected < len(self.candidates): return self.candidates[self.selected]
        return None

    def to_dict(self) -> dict[str,Any]:
        return {
            "index":self.index,"surface":self.surface,"mode":self.mode,
            "selected":self.selected,
            "decision_reason":self.decision_reason,
            "lexical_support":self.lexical_support,
            "decision_margin":self.decision_margin,
            "review_reason":self.review_reason,
            "review_priority":self.review_priority,
            "page":self.page,
            "paragraph":self.paragraph,
            "line":self.line,
            "source_line":self.source_line,
            "word_in_paragraph":self.word_in_paragraph,
            "legacy_location":self.legacy_location,
            "candidates":[c.to_dict() for c in self.candidates],
        }
