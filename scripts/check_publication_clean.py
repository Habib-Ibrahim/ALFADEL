from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

restricted_placeholders = [
    *sorted((ROOT / "resources" / "lexicon").glob("*.adz")),
    ROOT / "resources" / "orthography" / "Ortografia.adz",
    ROOT / "resources" / "controlled_corpus" / "controlled_corpus.jsonl",
    ROOT / "resources" / "reviewed_overlay" / "canonical_lexicon_additions.tsv",
]

forbidden_suffixes = {".an2", ".an3", ".anl"}
forbidden_name_fragments = ("PRIVATE_TEST", "FULL_PRIVATE", "training_corpus", "gold.tsv")

problems: list[str] = []

for path in restricted_placeholders:
    if not path.exists():
        problems.append(f"Missing required placeholder: {path.relative_to(ROOT)}")
    elif path.stat().st_size != 0:
        problems.append(
            f"Restricted placeholder is not empty: {path.relative_to(ROOT)} "
            f"({path.stat().st_size} bytes)"
        )

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if path.suffix.lower() in forbidden_suffixes:
        problems.append(f"Forbidden analysis/data file found: {rel}")
    upper_name = path.name.upper()
    if any(fragment.upper() in upper_name for fragment in forbidden_name_fragments):
        problems.append(f"Private/test file found: {rel}")
    if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
        problems.append(f"Python cache file found: {rel}")

if problems:
    print("PUBLICATION CHECK FAILED")
    for problem in problems:
        print(f" - {problem}")
    sys.exit(1)

print("PUBLICATION CHECK PASSED")
print("Restricted lexical/corpus placeholders are empty and no private analysis files were found.")
