# External resources — intentionally not distributed

The resource files committed in this directory are **zero-byte structural placeholders**. They document the layout expected by ALFADEL but contain none of the lexical or corpus resources used during development and testing.

- `lexicon/`: dictionaries and lexica developed within the **AAW project**. These resources are copyright **Don Davide Righi** and are not distributed with ALFADEL.
- `orthography/`: related orthographic resource data from the same restricted resource set; not distributed.
- `controlled_corpus/`: controlled corpus from the **e-Cheikho project**, developed between **CEDRAC** and **GREgORI**; not distributed.
- `reviewed_overlay/`: intentionally empty in the public release so that no lexical material derived from restricted resources is redistributed.

Users who are legally authorized to use the required resources may supply them locally. Do not commit populated copies to the public repository.

Before every public release, run:

```bash
python scripts/check_publication_clean.py
```
