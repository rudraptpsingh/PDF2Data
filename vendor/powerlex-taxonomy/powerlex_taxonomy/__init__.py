"""Read the PowerLex taxonomy: bodies, document types, statuses, glossary, instruments and rules.

    from powerlex_taxonomy import load
    tx = load()                       # dist/powerlex-taxonomy.json if built, else the taxonomy/ and rules/ files
    tx.body("Grid-India")             # -> {"code": "GRID-INDIA", ...}
    tx.type("CERC-15")["label"]       # -> "Record of proceedings"
    tx.classify(url, label, body, kind=None, title=None)  # -> {"type": "CERC-15", "rule": "cerc.rop", "confidence": "high", ...} or None
    tx.status("draft_notification")   # -> {"id": "draft_notified", "group": "draft", ...}
    tx.expand("GNA")                  # -> glossary entries
"""
from __future__ import annotations

import json
import re
import urllib.parse
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # vendored: the bundle sits next to this package


class Taxonomy:
    def __init__(self, data: dict):
        self.data = data
        self.version = data.get("version")
        self.bodies = data["bodies"]
        self.types = {t["id"]: t for t in data["document_types"]}
        self.statuses = data["statuses"]
        self.glossary = data["glossary"]
        self.source_rules = data["rules"]["source_rules"]
        self.selection_rules = data["rules"]["selection"]
        self.retrieval = data["rules"]["retrieval"]
        self.citation_classes = data["citation_classes"]
        self._spell = [(re.compile(r"\b" + re.escape(v) + r"\b", re.I), sp["canonical"])
                       for sp in data.get("spellings", []) for v in sorted(sp["variants"], key=len, reverse=True)]
        self.corrections = {(c["sha256"], c["field"]): c for c in data.get("corrections", [])}
        self._alias = {}
        for b in self.bodies:
            for a in [b["code"], *b.get("aliases", [])]:
                self._alias.setdefault(a.upper(), b["code"])
        self._host = {h: b["code"] for b in self.bodies for h in b.get("hosts", [])}
        self._status = {}
        for s in self.statuses:
            for a in [s["id"], *s.get("aliases", [])]:
                self._status.setdefault(a, s)
        self._role_types = {}
        for t in data["document_types"]:
            for r in t.get("registry_roles", []):
                self._role_types.setdefault(r, []).append(t["id"])

    def normalize(self, text):
        """Lower-case text with every known spelling variant replaced by its canonical form (for matching only)."""
        out = (text or "").lower()
        for rx, canon in self._spell:
            out = rx.sub(canon, out)
        return out

    def body(self, value):
        """Any spelling (code, Map org, Workbench name, alias, crawler partition) -> body record, or None."""
        if not isinstance(value, str) or not value.strip():
            return None
        v = value.strip().upper()
        code = self._alias.get(v) or (v if re.fullmatch(r"[A-Z]{2,6}ERC", v) else None)
        return next((b for b in self.bodies if b["code"] == code), {"code": code, "parent": "SERC"} if code else None)

    def body_of_url(self, url):
        host = urllib.parse.urlparse(url or "").netloc.lower()
        return self._host.get(host) or self._host.get(host.removeprefix("www."))

    def type(self, type_id):
        return self.types.get(type_id)

    def types_for_role(self, registry_role):
        """Map registry role (e.g. 'cerc/record-of-proceedings') -> taxonomy type ids."""
        return self._role_types.get(registry_role, [])

    def status(self, value):
        return self._status.get(value if value is not None else "")

    def classify(self, url=None, label=None, body=None, kind=None, title=None, filename=None):
        """Type from the full source signal. confidence: high (the label, name or folder states it) or low.

        Signals: ``label`` is the listing section the document was found under (e.g. CERC "Regulation (Current)");
        ``kind`` is the document's own label (the second segment of "<section> · <kind>", e.g. "Statement of
        Reasons"); ``title`` is the document's own title; the filename and path come from ``url`` (``filename``, e.g.
        a local upload's file name, is read as one more path segment when the URL does not end with it). A label written
        as "<section> · <kind>" is split, and the part after the last " · " is the kind unless ``kind`` is given.

        Precedence, highest first (within a tier, rules in file order):
          1. hosted rules: one page of a site (`hosted`, `match.path_all`), by the URL's host body whatever body was
             declared (a site can publish another body's documents);
          2. the own kind label against `match.label`;
          3. the document's name: `match.name` regexes against the path (filename included) and `match.title`
             regexes against the title;
          4. the listing section against `match.label`, and the folder (`match.path` substrings of the path).
        Each tier's first matching rule (file order) is that tier's answer. A high-confidence answer from a lower tier
        beats a low-confidence answer from a higher tier (a generic kind such as "Guidelines" is only a hint when the
        title names the scheme); with no high answer the highest tier's low answer stands.
        `match.path_all` substrings must all be in the path for any tier. `accepts` is the rule's list of types a
        reviewer may confirm instead. `issuer` is the body that issues the matched type (the type's body; for a
        state commission, the commission itself). `signal` names the tier that matched."""
        declared = self.body(body)["code"] if self.body(body) else None
        spec = declared or self.body_of_url(url)
        b = self._rules_body(spec)
        host = self._rules_body(self.body_of_url(url))
        path = self.normalize(_match_path(url, filename))
        section, own = _split_label(label)
        sec = self.normalize(section)
        kd = self.normalize((kind or "").strip() or own)
        ttl = self.normalize((title or "").strip())
        hosted = [r for r in self.source_rules if r.get("hosted") and host and r["body"] == host]
        plain = [r for r in self.source_rules if not r.get("hosted") and r["body"] == b]

        def ok(r):
            return all(p in path for p in r["match"].get("path_all", []))

        def labelled(r, text):
            return bool(text) and any(re.search(x, text) for x in r["match"].get("label", []))

        def named(r):
            m = r["match"]
            return (bool(path) and any(re.search(x, path) for x in m.get("name", []))) or \
                (bool(ttl) and any(re.search(x, ttl) for x in m.get("title", [])))

        def folder(r):
            m = r["match"]
            if not (m.get("path") or m.get("label") or m.get("name") or m.get("title")):
                return True
            return any(p in path for p in m.get("path", [])) or labelled(r, sec)

        tiers = [("hosted", hosted, lambda r: named(r) or folder(r)), ("kind", plain, lambda r: labelled(r, kd)),
                 ("name", plain, named), ("section", plain, folder)]
        first = None
        for signal, rules, hit in tiers:
            r = next((r for r in rules if ok(r) and hit(r)), None)
            if r is not None and r["confidence"] == "high":
                return self._hit(r, signal, spec, b)
            first = first or (r and (r, signal))
        return self._hit(*first, spec, b) if first else None

    def _hit(self, r, signal, spec, b):
        tb = (self.types.get(r["document_type"]) or {}).get("body") or r["body"]
        rolls_up = spec and (spec == tb or (self.body(spec) or {}).get("parent") == tb)
        issuer = tb if tb != r["body"] else (spec if rolls_up else tb)
        return {"type": r["document_type"], "rule": r["id"], "confidence": r["confidence"],
                "body": r["body"] if r.get("hosted") else b, "issuer": issuer, "signal": signal,
                "accepts": r.get("accepts", [r["document_type"]])}

    def _rules_body(self, code):
        """A body code as the source rules know it: a body without rules of its own uses its parent's (KERC -> SERC)."""
        if code and code not in {r["body"] for r in self.source_rules}:
            return (self.body(code) or {}).get("parent") or code
        return code

    def correction(self, sha256, field="document_type"):
        """A recorded correction for a stored value, or None."""
        return self.corrections.get((sha256, field))

    def expand(self, term):
        t = self.normalize((term or "").strip())
        return [e for e in self.glossary if self.normalize(e["term"]) == t]

    def priority(self, doc):
        """Selection priority from rules/selection.json. doc: type, year, status, cited_by, confidence."""
        t = self.types.get(doc.get("type") or "") or {}
        st = self.status(doc.get("status") or "") or {"group": "unknown"}
        for r in self.selection_rules:
            if "status_group" in r and st["group"] not in r["status_group"]:
                continue
            if "citation_class" in r and t.get("citation_class") not in r["citation_class"]:
                continue
            if "stage" in r and t.get("stage") not in r["stage"]:
                continue
            if "stage_not" in r and t.get("stage") in r["stage_not"]:
                continue
            if "type" in r and doc.get("type") not in r["type"]:
                continue
            if "type_not" in r and doc.get("type") in r["type_not"]:
                continue
            if r.get("type_missing") and doc.get("type") in self.types:
                continue
            y = doc.get("year") or 0
            if "year_min" in r and y < r["year_min"]:
                continue
            if "year_max" in r and (not y or y > r["year_max"]):
                continue
            if "cited_min" in r and (doc.get("cited_by") or 0) < r["cited_min"]:
                continue
            return r["priority"], r["reason"]
        return "P4", "No rule matched"


def _match_path(url, filename=None):
    """The URL path, decoded, plus the file a query names ('/whats-new/file?path=/a/b/Manual 2026.pdf' ->
    '/whats-new/file/Manual 2026.pdf': some sites serve every document through one script), plus a declared
    file name when the path does not already end with it."""
    u = urllib.parse.urlparse(url or "")
    path = urllib.parse.unquote(u.path)
    m = re.search(r"[^=&]*\.(pdf|docx?|xlsx?|zip)\b", urllib.parse.unquote(u.query), re.I)
    if m:
        path += "/" + m.group(0).split("/")[-1]
    name = re.split(r"[\\/]", (filename or "").strip())[-1]
    if name and not path.endswith("/" + name):
        path += "/" + name
    return path


def _split_label(label):
    """'<section> · <kind>' -> (section, kind); a plain label is the section with no kind."""
    parts = [p.strip() for p in (label or "").split("\u00b7")]
    parts = [p for p in parts if p]
    if len(parts) > 1:
        return " · ".join(parts[:-1]), parts[-1]
    return (parts[0] if parts else ""), ""


def assemble(root: Path = ROOT) -> dict:
    """Assemble the bundle from taxonomy/ and rules/ (what tools/build.py publishes to dist/)."""
    rd = lambda p: json.loads((root / p).read_text())
    return {
        "schema": "powerlex.taxonomy.v1",
        "bodies": rd("taxonomy/bodies.json")["bodies"],
        "document_types": rd("taxonomy/document-types.json")["document_types"],
        "question_vocabulary": rd("taxonomy/document-types.json")["question_vocabulary"],
        "citation_classes": rd("taxonomy/citation-classes.json")["citation_classes"],
        "statuses": rd("taxonomy/statuses.json")["statuses"],
        "instrument_kinds": rd("taxonomy/instrument-kinds.json"),
        "glossary": rd("taxonomy/glossary.json")["entries"],
        "spellings": rd("taxonomy/spellings.json")["spellings"],
        "instruments": rd("taxonomy/instruments.json"),
        "rules": {"source_rules": rd("rules/source-rules.json")["rules"], "selection": rd("rules/selection.json")["rules"],
                  "retrieval": rd("rules/retrieval.json")},
        "corrections": [c for f in sorted((root / "corrections").glob("*.json")) for c in json.loads(f.read_text())["corrections"]],
        # Kept on record so a consumer that applied an earlier version's correction can undo it; never applied.
        "withdrawn_corrections": [c for f in sorted((root / "corrections").glob("*.json"))
                                  for c in json.loads(f.read_text()).get("withdrawn", [])],
    }


@lru_cache(maxsize=4)
def load(path: "str | None" = None) -> Taxonomy:
    if path:
        return Taxonomy(json.loads(Path(path).read_text()))
    dist = ROOT / "powerlex-taxonomy.json"
    if dist.exists():
        return Taxonomy(json.loads(dist.read_text()))
    return Taxonomy(assemble())


def assert_known(tx: "Taxonomy", bodies=(), types=(), statuses=(), registry_roles=()):
    """Contract test helper for consumers: raise AssertionError listing every id the taxonomy does not know."""
    roles = {r for t in tx.types.values() for r in t.get("registry_roles", [])}
    bad = ([f"body {b!r}" for b in bodies if not tx.body(b)] + [f"type {t!r}" for t in types if t not in tx.types] +
           [f"status {s!r}" for s in statuses if not tx.status(s)] + [f"registry role {r!r}" for r in registry_roles if r not in roles])
    if bad:
        raise AssertionError(f"Unknown to powerlex-taxonomy {tx.version}: " + ", ".join(bad))
