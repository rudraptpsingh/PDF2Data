"""Route extraction failures to different repair steps, without promoting text."""
from __future__ import annotations
from collections import Counter
from importlib.resources import files
import json


def matching_routes(row: dict, policy: dict | None = None) -> list[dict]:
    """Return ordered policy routes for one page, preserving explicit priority."""
    policy = policy or json.loads(files("local_vector_batch").joinpath("quality_routes.json").read_text())
    if policy.get("schema_version") != 1:
        raise ValueError("unknown extraction repair routing policy")
    components = row.get("components", {})
    reasons = {value.get("reason") for value in components.values() if value.get("state") != "pass"}
    matched = []
    for rule in policy["routes"]:
        predicates = []
        if "failed" in rule:
            predicates.append(any(components.get(name, {}).get("state") in {"fail", "unknown"}
                                  for name in rule["failed"]))
        if "reasons" in rule:
            predicates.append(bool(reasons.intersection(rule["reasons"])))
        if "all_pass" in rule:
            predicates.append(all(components.get(name, {}).get("state") == "pass" for name in rule["all_pass"]))
        if predicates and all(predicates):
            matched.append(rule)
    return matched


def repair_patterns(rows: list[dict], policy: dict | None = None) -> dict:
    policy = policy or json.loads(files("local_vector_batch").joinpath("quality_routes.json").read_text())
    if policy.get("schema_version") != 1:
        raise ValueError("unknown extraction repair routing policy")
    primary, patterns, actions = Counter(), Counter(), {}
    near_pass = 0
    for row in rows:
        if row.get("outcome") != "repair_required":
            continue
        matched = [rule["id"] for rule in matching_routes(row, policy)]
        for rule in matching_routes(row, policy):
            actions[rule["id"]] = rule["action"]
        primary[matched[0] if matched else "unclassified"] += 1
        patterns.update(matched)
        near_pass += "verified_text_visual_gap" in matched
    return {"primary_counts": dict(primary), "overlapping_pattern_counts": dict(patterns),
            "near_pass_text_pages": near_pass, "actions": actions,
            "empty_text_is_confirmed_blank": False,
            "interpretation": "Primary counts are disjoint; pattern counts overlap. These are repair routes, not completed repairs."}
