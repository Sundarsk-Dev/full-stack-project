"""Versioned rules across eight groups; unsupported/contextual wording abstains."""
import json
from pathlib import Path
import re

from .scoring import APPLICABLE, CATEGORY_NAMES, RUBRIC_REVISION, SUPPORTED_GROUPS, compute_scores
RULE_DATA = json.loads((Path(__file__).parent / "rules" / "gate1.json").read_text(encoding="utf-8"))
ADDITIONAL_RULES = json.loads((Path(__file__).parent / "rules" / "gate2.json").read_text(encoding="utf-8"))
RULES = {r["sentence"]: r for r in RULE_DATA["rules"] + ADDITIONAL_RULES["rules"]}
ENGINE = {"extractor": "paste-v1", "segmenter": "sentences-v1", "rules": ADDITIONAL_RULES["revision"], "library": "empty-v1", "rubric": RUBRIC_REVISION}
CONTEXT_GUARD = re.compile(r"\b(except|unless|notwithstanding|however|subject to|defined as|means|in this policy|does not apply|do not apply|false|fictional|hypothetical|example statements|does not describe)\b", re.I)
HEADINGS = {"privacy policy", "terms and conditions", "cookie policy", "refund policy", "data sharing", "data retention", "tracking and consent", "contact us", "definitions", "content license", "subscriptions", "changes to terms", "disputes", "account closure"}


def normalize(sentence):
    return " ".join(sentence.strip().rstrip(".!?").lower().split())


def segment(text):
    clauses = []
    for line in re.finditer(r"[^\r\n]+", text):
        for sentence in re.finditer(r".+?(?:[.!?](?=\s|$)|$)", line.group()):
            raw = sentence.group()
            if not raw.strip():
                continue
            start = line.start() + sentence.start() + len(raw) - len(raw.lstrip())
            end = line.start() + sentence.end() - len(raw) + len(raw.rstrip())
            value = text[start:end]
            substantive = normalize(value) not in HEADINGS
            clauses.append({"id": len(clauses) + 1, "start": start, "end": end, "text": value,
                            "substantive": substantive, "unrated_reason": "unsupported_wording" if substantive else "heading_or_navigation", "findings": []})
            if len(clauses) > 2000:
                raise ValueError("Use a policy with at most 2,000 clauses.")
    return clauses


def analyze(text: str, document_type: str, *, extractor="paste-v1", headings=()) -> dict:
    if document_type not in APPLICABLE:
        raise ValueError("Unsupported document type.")
    if not text.strip() or len(text) > 200000 or len(text.split()) > 25000:
        raise ValueError("Enter a policy within 200,000 characters and 25,000 words.")
    clauses = segment(text)
    for clause in clauses:
        if any(h["start"] <= clause["start"] < clause["end"] <= h["end"] for h in headings):
            clause["substantive"], clause["unrated_reason"] = False, "heading_or_navigation"
    context_guarded = bool(CONTEXT_GUARD.search(text))
    candidates = [(c, RULES.get(normalize(c["text"]))) for c in clauses if c["substantive"]]
    issue_severities = {}
    for _, rule in candidates:
        if rule:
            issue_severities.setdefault(rule["issue"], set()).add(rule["severity"])
    conflicting = {issue for issue, values in issue_severities.items() if 0 in values and any(v > 0 for v in values)}
    for clause, rule in candidates:
        if context_guarded:
            clause["unrated_reason"] = "context_requires_review"
        elif rule and rule["issue"] in conflicting:
            clause["unrated_reason"] = "conflicting_statements"
        elif rule:
            severity = rule["severity"]
            clause["findings"] = [{"category": rule["category"], "severity": severity,
                "rating": "favorable" if severity == 0 else "neutral" if severity == 1 else "concerning" if severity <= 3 else "invasive",
                "quote": clause["text"], "start": clause["start"], "end": clause["end"],
                "explanation": rule["explanation"], "source": "rule", "rule_id": rule["id"]}]
            clause["unrated_reason"] = None
    categories, score = compute_scores(clauses, document_type)
    return {"text": text, "clauses": clauses, "categories": categories, "score": score,
            "supported_groups": sorted(SUPPORTED_GROUPS), "engine": {**ENGINE, "extractor": extractor}}
