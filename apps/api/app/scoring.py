"""The frozen rubric, independent of rule matching, storage and provider calls."""

CATEGORY_NAMES = ["Sharing / selling", "Retention / deletion", "Tracking / consent", "Content licensing / AI training", "Renewal / refunds / cancellation", "Unilateral changes", "Arbitration / class waivers", "Termination / export"]
APPLICABLE = {"terms": set(range(1, 9)), "privacy": {1, 2, 3, 4, 6}, "cookies": {1, 2, 3, 6}, "refund": {5, 6}}
SUPPORTED_GROUPS = frozenset(range(1, 9))
RUBRIC_REVISION = "plan-2026-10-26-v1"
PROFILE_REVISION = "plan-2026-10-26-profiles-v1"
PROFILE_WEIGHTS = {"balanced": (1,) * 8, "privacy_first": (25, 20, 20, 15, 5, 5, 5, 5)}


def band(score):
    return next(letter for threshold, letter in [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")] if score >= threshold)


def weights_for(document_type, profile):
    if document_type not in APPLICABLE or profile not in PROFILE_WEIGHTS:
        raise ValueError("Unsupported document type or score profile.")
    weights = PROFILE_WEIGHTS[profile]
    return {category: weights[category - 1] for category in sorted(APPLICABLE[document_type])}


def score_categories(categories, rule_ids, document_type, coverage, consistent, *, profile="balanced"):
    weights = weights_for(document_type, profile)
    if len(categories) != 8 or {c["id"] for c in categories} != set(range(1, 9)):
        raise ValueError("Scores require all eight categories, including unknowns.")
    scoped = [category for category in categories if category["id"] in weights]
    total = sum(weights.values())  # Fixed scope, including every unknown category.
    best = 100 - 20 * sum(c["max_severity"] * weights[c["id"]] for c in scoped) / total
    worst = 100 - 20 * sum((5 if c["unknown"] else c["max_severity"]) * weights[c["id"]] for c in scoped) / total
    ids = set(rule_ids)
    cap = 59 if {"perpetual-license", "mandatory-arbitration"} <= ids else 100
    if "sell-data" in ids:
        cap = min(cap, 74)
    best, worst = round(min(cap, best), 2), round(min(cap, worst), 2)
    grade = band(best) if coverage >= .8 and consistent and band(best) == band(worst) else None
    return {"best": best, "worst": worst, "grade": grade, "coverage": coverage, "scope_consistent": consistent}


def compute_scores(clauses, document_type):
    applicable = APPLICABLE[document_type]
    substantive = [c for c in clauses if c["substantive"]]
    findings = [f for c in substantive for f in c["findings"]]
    unresolved = any(not c["findings"] for c in substantive)
    categories = []
    for category, name in enumerate(CATEGORY_NAMES, 1):
        matches = [f for f in findings if f["category"] == category]
        categories.append({"id": category, "name": name, "applicable": category in applicable,
                           "max_severity": max((f["severity"] for f in matches), default=0),
                           "unknown": not matches or unresolved or category not in SUPPORTED_GROUPS,
                           "accepted_count": len({(f["rule_id"], f["severity"]) for f in matches})})
    coverage = sum(bool(c["findings"]) for c in substantive) / len(substantive) if substantive else 0
    consistent = all(f["category"] in applicable for f in findings)
    score = score_categories(categories, (f["rule_id"] for f in findings), document_type, coverage, consistent)
    return categories, score


def saved_profiles(view, document_type):
    """Project saved aggregates; never reinterpret text or replace the stored balanced score."""
    score = view["score"]
    balanced = {"score": dict(score), "weights": {}}
    profiles = {"revision": None, "balanced": balanced}
    categories = view["categories"]
    # Unknown historical rubrics/shapes remain readable without inventing a new profile.
    if (view["engine"].get("rubric") != RUBRIC_REVISION or document_type not in APPLICABLE
            or len(categories) != 8 or {c["id"] for c in categories} != set(range(1, 9))
            or any(c["applicable"] != (c["id"] in APPLICABLE[document_type]) for c in categories)):
        return profiles
    profiles["revision"] = PROFILE_REVISION
    rule_ids = {f["rule_id"] for c in view["clauses"] if c["substantive"] for f in c["findings"]}
    for profile in PROFILE_WEIGHTS:
        weights = weights_for(document_type, profile)
        profiles[profile] = {"weights": {key: value / sum(weights.values()) for key, value in weights.items()},
                            "score": dict(score) if profile == "balanced" else score_categories(
                                categories, rule_ids, document_type, score["coverage"], score["scope_consistent"], profile=profile)}
    return profiles
