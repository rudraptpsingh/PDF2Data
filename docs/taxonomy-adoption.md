# powerlex-taxonomy adoption

PDF2Data pins **powerlex-taxonomy 0.2.0** (bundle sha256 `1fa2ed7763d37a72053d4cacb46c7edf9026dfbf6dc6db7367773adf71c424d1`).
The vendored copy lives in `vendor/powerlex-taxonomy/`; `PIN.json` carries only `version`, `sha256` and `source`.

## What changes at runtime

Nothing. PDF2Data does not choose document types: Map picks a taxonomy type, resolves the rule files the
taxonomy lists for it (`pdf2data_rule_files`) through its registry snapshot, and passes one to PDF2Data with
`--rules` plus a declared family. No code under `src/` reads the vendored taxonomy, so extraction, indexing
inputs and outputs are identical to `origin/main`. There is no switch to enable because no behaviour is
gated on the taxonomy.

## What the contract test checks

`tests/test_powerlex_taxonomy_contract.py`:

1. `PIN.json` version and sha256 equal the bundle's own; `PIN.json` holds no local path; the generated
   Map slice's `generated_from` stamp names the same version and sha256; no vendored file holds an
   absolute local path.
2. The shared taxonomy cases (bodies, parents, classify, statuses, priority, glossary, spelling) pass
   against the vendored reader.
3. Every taxonomy id PDF2Data's code, rules and fixtures use is known, and every declared family is a
   taxonomy registry-role name or a documented PDF2Data-local family.
4. The rule files the taxonomy lists per type: 45 distinct references in 0.2.0, of which 18 are under
   `../extraction-profiles/native/`, 26 under `native/` and 1 is a `map-addendum:` reference.
5. Each of those references resolves through Map's registry snapshot, matches its recorded sha256 and loads
   with PDF2Data's own rule loader. This needs an explicit Map checkout:

   ```sh
   MAP_ROOT=<path to a powerlex-map checkout> PYTHONPATH=src python -m pytest tests/test_powerlex_taxonomy_contract.py
   MAP_ROOT=<path to a powerlex-map checkout> PYTHONPATH=src python tests/test_powerlex_taxonomy_contract.py   # JSON report
   ```

   Without `MAP_ROOT` (or when it has no `data/snapshot.json`) the check reports `status: "not_run"` and the
   pytest case is skipped; it never reports `pass`. There is no fallback to a sibling `powerlex-map`
   directory, so the result does not depend on which branch that checkout happens to be on.

## Updating the pin

```sh
python3 ../powerlex-taxonomy/tools/vendor.py --into . --lang py
python3 ../powerlex-taxonomy/tools/vendor.py --into . --lang py --check
```

Then update the expected counts in `test_rule_file_reference_counts_by_path` if the taxonomy's rule-file
references changed (see the taxonomy CHANGELOG).
