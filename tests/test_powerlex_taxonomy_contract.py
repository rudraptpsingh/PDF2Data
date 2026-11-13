"""Contract test against the vendored powerlex-taxonomy (vendor/powerlex-taxonomy).

PDF2Data does not choose document types itself: Map picks a taxonomy type, looks up the rule files the
taxonomy lists for it (`pdf2data_rule_files`), resolves them through its registry snapshot to
content-addressed files, and passes one to PDF2Data with `--rules` plus a declared family. This test checks
that hand-off without changing any extraction behaviour:

1. The shared taxonomy cases run against the vendored reader.
2. Every taxonomy id PDF2Data's code, rules and fixtures use is known (assert_known).
3. Every declared family PDF2Data's rules and fixtures use is either a taxonomy registry-role name or a
   documented PDF2Data-local family (new unknown families fail).
4. Every rule file the taxonomy lists per type exists (in PDF2Data, or through Map's registry snapshot),
   matches its recorded sha256, and parses with PDF2Data's own rule loader. Gaps are reported; only gaps
   not in KNOWN_RULE_FILE_GAPS fail. The Map checkout is named explicitly with MAP_ROOT; without it the
   check reports status "not_run" (pytest skip), never "pass", so the result never depends on whichever
   branch a sibling Map checkout happens to be on.

Update the vendored copy with:
    python3 ../powerlex-taxonomy/tools/vendor.py --into . --lang py
Print the rule-file report with:
    MAP_ROOT=<powerlex-map checkout> PYTHONPATH=src python tests/test_powerlex_taxonomy_contract.py
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "vendor/powerlex-taxonomy"
sys.path.insert(0, str(VENDOR))
from powerlex_taxonomy import Taxonomy, assert_known  # noqa: E402

TX = Taxonomy(json.loads((VENDOR / "powerlex-taxonomy.json").read_text()))
CASES = {p.stem: json.loads(p.read_text()) for p in sorted((VENDOR / "cases").glob("*.json"))}
ROLES = {r for t in TX.types.values() for r in t.get("registry_roles", [])}
ROLE_NAMES = {r.split("/")[-1] for r in ROLES}

# Families PDF2Data's own rules, code and tests declare that are not taxonomy registry-role names.
# They are PDF2Data-local format scopes (or test-only values), not stored taxonomy ids.
PDF2DATA_LOCAL_FAMILIES = {"generic", "notice", "judgment", "financial_evidence"}
# Taxonomy rule-file references known to be unresolved. Keep empty unless a gap is accepted and recorded.
KNOWN_RULE_FILE_GAPS: set = set()

SCAN_DIRS = ("src", "tests", "schemas", "examples")
SKIP_PARTS = {"__pycache__", "node_modules", ".git", "vendor"}
TYPE_ID = re.compile(r"\b(?:CERC|SERC|MOP|MoP|CEA|APTEL|CTUIL|CTU|GRID-INDIA|MNRE|SECI|BEE|PFC|REC|NLDC|IEX|PXIL)-\d{2}\b")
ROLE_ID = re.compile(r"[\"'](?:cerc|serc|mop|cea|aptel|ctuil|grid-india|mnre|seci|bee|generic)/[a-z0-9-]+[\"']")
# A declared family: family='x', 'family':'x', declared_family=..., families=['x', 'y'] (string lists only;
# dict-form source families in legal_l1 tests are test-local ids).
FAMILY = re.compile(r"""(?<![\w-])(?:declared_)?famil(?:y|ies)['"]?\s*[:=]\s*(\[\s*(?:['"][^'"]+['"]\s*,?\s*)*\]|['"][^'"]+['"])""")


def _repo_files():
    for d in SCAN_DIRS:
        root = REPO / d
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".md"} and not SKIP_PARTS & set(p.parts):
                if p.resolve() == Path(__file__).resolve():
                    continue
                yield p


def _repo_text():
    for p in _repo_files():
        try:
            yield p, p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def used_taxonomy_ids():
    types, roles = set(), set()
    for _, text in _repo_text():
        types.update(TYPE_ID.findall(text))
        roles.update(m.strip("\"'") for m in ROLE_ID.findall(text))
    return types, roles


def used_families():
    found = set()
    for _, text in _repo_text():
        for m in FAMILY.findall(text):
            found.update(v for v in re.findall(r"['\"]([^'\"]+)['\"]", m)
                         if re.fullmatch(r"[a-z][a-z0-9_]*", v))
    # Families the bundled extraction rules declare.
    for p in (REPO / "src/local_vector_batch/extraction_rules").glob("*.json"):
        rules = json.loads(p.read_text(encoding="utf-8"))
        for f in rules.get("families", []):
            found.add(f if isinstance(f, str) else f.get("id"))
        for field in rules.get("fields", []):
            found.update(field.get("families", []))
    return {f for f in found if f}


def map_dir():
    """The Map checkout named by MAP_ROOT, or None. There is deliberately no sibling-directory fallback."""
    env = os.environ.get("MAP_ROOT", "").strip()
    return Path(env).expanduser() if env else None


def ref_prefix(ref):
    if ref.startswith("map-addendum:"):
        return "map-addendum"
    return ref.rsplit("/", 1)[0] + "/" if "/" in ref else "(bare)"


def map_bindings(root):
    snap = root / "data/snapshot.json"
    if not snap.exists():
        return None
    out = {}

    def walk(o):
        if isinstance(o, dict):
            if "original_reference" in o and "sha256" in o and "path" in o:
                out.setdefault(o["original_reference"], {})[o["sha256"]] = o["path"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(snap.read_text(encoding="utf-8")))
    return out


def rule_file_report(map_root=None):
    """For every pdf2data_rule_files reference in the taxonomy: where it resolves and whether PDF2Data parses it."""
    from local_vector_batch.field_rules import load as load_rules
    map_root = Path(map_root) if map_root else map_dir()
    bindings = map_bindings(map_root) if map_root else None
    local = {p.name: p for p in (REPO / "src/local_vector_batch/extraction_rules").glob("*.json")}
    refs = {}
    for t in TX.types.values():
        for ref in t.get("pdf2data_rule_files") or []:
            refs.setdefault(ref, []).append(t["id"])
    rows = []
    for ref, types in sorted(refs.items()):
        row = {"ref": ref, "types": sorted(types), "resolved": [], "problems": []}
        name = ref.rsplit("/", 1)[-1]
        if name in local:
            row["resolved"].append({"where": "pdf2data", "path": str(local[name].relative_to(REPO))})
        for sha, rel in (bindings or {}).get(ref, {}).items():
            path = map_root / rel
            entry = {"where": "powerlex-map", "path": rel, "sha256": sha}
            if not path.exists():
                row["problems"].append(f"bound file missing: {rel}")
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                row["problems"].append(f"sha256 differs: {rel}")
            try:
                rules = load_rules(path)
                entry.update(rule_set_id=rules["id"], families=sorted(
                    set(rules.get("families", [])) | {f for fld in rules["fields"] for f in fld.get("families", [])}))
            except Exception as e:  # noqa: BLE001 - reported, not raised
                row["problems"].append(f"PDF2Data cannot load {rel}: {e}")
            row["resolved"].append(entry)
        if not row["resolved"]:
            row["problems"].append("not found in PDF2Data" + ("" if bindings is None else " or Map's registry snapshot"))
        rows.append(row)
    counts = {}
    for r in rows:
        counts[ref_prefix(r["ref"])] = counts.get(ref_prefix(r["ref"]), 0) + 1
    problems = [r for r in rows if r["problems"]]
    if bindings is None:
        status = "not_run"
        reason = ("MAP_ROOT not set" if map_root is None
                  else "MAP_ROOT has no data/snapshot.json")
    else:
        status = "fail" if any(r["ref"] not in KNOWN_RULE_FILE_GAPS for r in problems) else "pass"
        reason = None
    return {"taxonomy_version": TX.version, "taxonomy_sha256": TX.data.get("sha256"),
            "status": status, "reason": reason,
            # Map's snapshot path is reported relative to MAP_ROOT so no machine path is written out.
            "map_snapshot": None if bindings is None else "$MAP_ROOT/data/snapshot.json",
            "rule_files": len(rows), "by_path_prefix": dict(sorted(counts.items())),
            "with_problems": problems, "rows": rows}


# 1. Shared cases --------------------------------------------------------------------------------------

def test_vendored_pin_matches_bundle():
    pin = json.loads((VENDOR / "PIN.json").read_text())
    assert (pin["version"], pin["sha256"]) == (TX.version, TX.data["sha256"])
    # PIN carries version, sha256 and source only: no local paths.
    assert set(pin) <= {"version", "sha256", "source"}, sorted(pin)
    assert not any("/Users/" in str(v) or str(v).startswith("/") for v in pin.values()), pin


def test_generated_slice_stamp_matches_pin():
    pin = json.loads((VENDOR / "PIN.json").read_text())
    for p in sorted((VENDOR / "generated").glob("*.json")):
        stamp = json.loads(p.read_text()).get("generated_from", "")
        assert stamp == f"powerlex-taxonomy {pin['version']} sha256 {pin['sha256']}", (p.name, stamp)


def test_vendored_files_hold_no_absolute_local_paths():
    for p in VENDOR.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            assert "/Users/" not in p.read_text(encoding="utf-8", errors="ignore"), p


def test_case_bodies():
    for spelling, code in CASES["bodies"]:
        assert (TX.body(spelling) or {}).get("code") == code, spelling


def test_case_parents():
    for code, parent in CASES["parents"]:
        assert TX.body(code).get("parent") == parent, code


def test_case_classify():
    for c in CASES["classify"]:
        got = TX.classify(c["url"], c["label"], c["body"], c.get("kind"), c.get("title"), c.get("filename")) or {}
        assert got.get("type") == c["type"], c["url"]
        if "confidence" in c:
            assert got.get("confidence") == c["confidence"], c["url"]
        if "accepts" in c:
            assert c["accepts"] in got.get("accepts", []), c["url"]
        if "issuer" in c:
            assert got.get("issuer") == c["issuer"], c["url"]
        if "signal" in c:
            assert got.get("signal") == c["signal"], c["url"]


def test_case_statuses():
    for alias, sid, group in CASES["statuses"]:
        s = TX.status(alias)
        assert (s["id"], s["group"]) == (sid, group), alias


def test_case_priority():
    for doc, p in CASES["priority"]:
        assert TX.priority(doc)[0] == p, doc


def test_case_glossary():
    for term, full in CASES["glossary"]:
        got = TX.expand(term)
        assert got and (got[0].get("full_form") or got[0]["term"]) == full, term


def test_case_spelling():
    for raw, norm in CASES["spelling"]:
        assert TX.normalize(raw) == norm, raw


def test_all_case_files_are_covered():
    assert set(CASES) <= {"bodies", "parents", "classify", "statuses", "priority", "glossary", "spelling"}, set(CASES)


# 2. Ids PDF2Data uses ------------------------------------------------------------------------------------

def test_taxonomy_ids_used_by_pdf2data_are_known():
    types, roles = used_taxonomy_ids()
    # The bundled CERC sharing-supplement rules name their issuer; the declared "draft" family is a status.
    assert_known(TX, bodies=["CERC"], types=sorted(types), statuses=["draft"], registry_roles=sorted(roles))


def test_declared_families_are_taxonomy_roles_or_documented_local():
    families = used_families()
    assert families, "family scan found nothing; the scan pattern is stale"
    unknown = {f for f in families if f.replace("_", "-") not in ROLE_NAMES} - PDF2DATA_LOCAL_FAMILIES
    assert not unknown, (f"families not in powerlex-taxonomy {TX.version} registry roles: {sorted(unknown)}; "
                         "use the taxonomy role name or add to PDF2DATA_LOCAL_FAMILIES with a reason")


# 3. Rule files the taxonomy lists per type ---------------------------------------------------------------

def test_taxonomy_rule_file_references_are_well_formed():
    for t in TX.types.values():
        for ref in t.get("pdf2data_rule_files") or []:
            assert ref.endswith(".rules.json") or ref.startswith("map-addendum:"), (t["id"], ref)


def test_rule_file_reference_counts_by_path():
    # powerlex-taxonomy 0.2.0: 45 distinct references (verifier counts). Update when the pin moves.
    report = rule_file_report()
    assert report["rule_files"] == 45, report["by_path_prefix"]
    assert report["by_path_prefix"] == {
        "../extraction-profiles/native/": 18, "map-addendum": 1, "native/": 26}, report["by_path_prefix"]


def test_rule_file_check_is_not_run_without_map_root(monkeypatch):
    monkeypatch.delenv("MAP_ROOT", raising=False)
    report = rule_file_report()
    assert report["status"] == "not_run" and report["map_snapshot"] is None, report["status"]


def test_taxonomy_rule_files_exist_and_load_or_are_reported():
    report = rule_file_report()
    if report["status"] == "not_run":
        pytest.skip(f"not_run: {report['reason']} (set MAP_ROOT to a powerlex-map checkout); "
                    "no taxonomy rule file is held inside PDF2Data itself")
    new_gaps = {r["ref"]: r["problems"] for r in report["with_problems"] if r["ref"] not in KNOWN_RULE_FILE_GAPS}
    assert not new_gaps, json.dumps(new_gaps, indent=1)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    rep = rule_file_report(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(rep, indent=1))
    sys.exit(1 if rep["status"] == "fail" else 0)
