"""Validate and shape persisted review metadata at the F5 -> F6 boundary."""
from .ai_contract import snapshot, validate_response, ReviewBatch
from .ai_ledger import digest
from .ai_review import MESSAGES
from .scoring import compute_scores
from .models import AICall


def baseline_snapshot(result):
    return {**{key: result[key] for key in ("engine", "supported_groups", "score", "categories")},
            "clauses": [{k: v for k, v in clause.items() if k != "text"} for clause in result["clauses"]]}


def finding_key(clause_id, finding):
    return clause_id, digest(finding)


def review_metadata(db, job, version, result, review):
    baseline = review.baseline if review else result
    if (baseline["text"] != version.text or baseline["engine"] != result["engine"]
            or result["engine"]["extractor"] != version.extractor_version):
        raise ValueError("The analysis did not preserve its source text.")
    for data in (baseline, result):
        categories, score = compute_scores(data["clauses"], job.document_type)
        if categories != data["categories"] or score != data["score"]:
            raise ValueError("The report scores do not match its findings.")
        for clause in data["clauses"]:
            if not (0 <= clause["start"] < clause["end"] <= len(version.text)) or version.text[clause["start"]:clause["end"]] != clause["text"]:
                raise ValueError("Evidence could not be matched to the source.")
            for f in clause["findings"]:
                if not (clause["start"] <= f["start"] < f["end"] <= clause["end"]) or version.text[f["start"]:f["end"]] != f["quote"]:
                    raise ValueError("Evidence could not be matched to the source.")
    if any(f["source"] == "ai" for c in baseline["clauses"] for f in c["findings"]):
        raise ValueError("The rules baseline contains external findings.")
    shape = lambda data: [(c["id"], c["start"], c["end"], c["substantive"]) for c in data["clauses"]]
    if shape(baseline) != shape(result) or any(
            original["findings"] != [f for f in current["findings"] if f["source"] != "ai"]
            for original, current in zip(baseline["clauses"], result["clauses"])):
        raise ValueError("The report did not preserve its rules baseline.")
    ai_findings = [(c["id"], f) for c in result["clauses"] for f in c["findings"] if f["source"] == "ai"]
    if len({(i, f["category"]) for i, f in ai_findings}) != len(ai_findings):
        raise ValueError("Duplicate AI findings cannot be published.")
    unresolved = sum(c["substantive"] and not c["findings"] for c in result["clauses"])
    fields = {"consent_provider": job.consent_provider, "consent_at": job.consent_at,
        "rules_baseline": baseline_snapshot(baseline), "unresolved_count": unresolved,
        "accepted_ai_findings": len(ai_findings), "ai_provenance": [], "effective_mode": "rules_only",
        "review_status": "not_requested", "degradation_reason": None}
    allowed = {}
    if review is None:
        if job.requested_mode != "rules_only" or ai_findings:
            raise ValueError("External review metadata is required.")
        return fields, allowed, "done"
    if (review.result != result or review.requested_mode != job.requested_mode
            or review.effective_mode not in {"rules_only", "hybrid"}
            or review.status not in {"not_requested", "not_needed", "reviewed", "limited", "degraded"}
            or review.unresolved_count != unresolved or review.accepted_ai_findings != len(ai_findings)
            or (review.degradation_reason is not None and review.degradation_reason not in MESSAGES)):
        raise ValueError("Review metadata does not match the report.")
    if review.effective_mode == "hybrid":
        if (job.requested_mode != "hybrid" or not job.consent or job.consent_provider != "groq"
                or job.consent_at is None or review.degradation_reason or not review.provenance):
            raise ValueError("Hybrid publication requires consent and provenance.")
    elif ai_findings:
        raise ValueError("Rules-only publication cannot include AI findings.")
    if bool(review.degradation_reason) != (review.status == "degraded"):
        raise ValueError("Degradation status is inconsistent.")
    for provenance in review.provenance:
        call = db.get(AICall, provenance["id"])
        if (call is None or call.job_id != job.id or call.version_id != version.id or call.state != "received"
                or call.snapshot != snapshot(baseline) or digest(call.request_payload) != call.batch_hash):
            raise ValueError("AI provenance does not belong to this report.")
        stored = {key: getattr(call, key) for key in ("id", "provider", "model", "prompt_revision", "snapshot", "clause_ids")}
        if stored != provenance:
            raise ValueError("AI provenance was changed.")
        batch = ReviewBatch(tuple(call.clause_ids), call.snapshot, call.request_payload, call.input_reserved, call.output_reserved)
        findings, _ = validate_response(call.response_text, batch, baseline)
        for finding in findings:
            local_id = finding["clause_id"]
            allowed[finding_key(local_id, {k: v for k, v in finding.items() if k != "clause_id"})] = call.id
        fields["ai_provenance"].append(stored)
    if any(finding_key(local_id, f) not in allowed for local_id, f in ai_findings):
        raise ValueError("AI findings do not match their saved validated response.")
    fields.update(effective_mode=review.effective_mode, review_status=review.status, degradation_reason=review.degradation_reason)
    return fields, allowed, "partial" if review.status in {"limited", "degraded"} else "done"
