# ALFADEL v0.1.2 — Initial public release

ALFADEL v0.1.2 is the first public publication snapshot of the application.

## Editorial workflows

- **Correct and standardize the text**: identifies probable OCR/HTR errors and historical spellings and proposes canonical standard readings.
- **Correct while preserving historical forms**: preserves already-historical spellings while presenting standard forms with licensed historical alternatives as explicit editorial choices.

Preserve mode visually distinguishes:

- `historical`;
- `standard → historical option`;
- `correction`;
- `unresolved`.

## Linguistic analysis

The release includes lexicon-aware candidate generation, Arabic morphology, clitic and pronominal-suffix handling, historical-orthography rules, OCR/HTR-sensitive fuzzy matching, and evidence-based candidate ranking. Development was driven by iterative testing on real historical Arabic text and by converting recurrent false positives into more general linguistic constraints.

## Interface and formats

- local browser interface;
- `.txt` and `.docx` import;
- corrected `.txt` export;
- review `.tsv` export;
- visible analysis-progress feedback;
- explicit human approval for every proposed replacement.

## Restricted resources

The dictionaries and lexica developed within the AAW project (copyright Don Davide Righi) are not distributed. The controlled corpus from the e-Cheikho project, developed between CEDRAC and GREgORI, is also not distributed. The repository contains zero-byte placeholders only.
