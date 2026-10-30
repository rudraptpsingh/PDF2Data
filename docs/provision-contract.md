# Complete provision candidates and exact children

`provisions.py` adds `powerlex.provisions.v1` without changing the existing
page-based evidence contract. New extraction packages include an optional,
checksummed `provision-candidates.json` artifact. Older packages still verify.

Lexical top-level headings group wrapped lines and continuation pages for
navigation. Every such wrapper has candidate boundaries and unresolved
semantics. Quoted numbering can belong to another instrument. Clause 1 has
no automatic exclusion. Raw pages, table cells and source hashes stay intact.

`select_provision()` accepts explicit, ordered codepoint selections and a
separate boundary-review receipt. `validate_wrapper()` rejects stale hashes,
inexact quotes, overlapping spans and unsupported complete-boundary claims.
A receipt identifies a review; it does not itself establish legal correctness.
Semantic review and publication eligibility remain separate responsibilities.

`provision_children()` emits exact source slices linked to their parent.
`embedding_inputs()` adapts these slices to the existing `passages` input mode
with page/PDF hashes and parent IDs in scalar metadata. Short meaningful
units survive this mode. The existing encoder still rejects token overflow;
no model, dimensions or vectors are fabricated by this export.

The source-checked July/October Sharing pilot, additive local import and
cross-repository validation runner live in the sibling Cloudflare application:
`powerlex-site/scripts/run_regulatory_local_validation.py`. Set
`LOCAL_VECTOR_BATCH_ROOT` there if the parser checkout is not a sibling.

Focused parser checks:

```sh
.venv/bin/python -m pytest -q tests/test_provisions.py tests/test_evidence.py \
  tests/test_evidence_package.py tests/test_reviewed_evidence.py \
  tests/test_structure_candidates.py tests/test_model_budget.py tests/test_token_windows.py
```
