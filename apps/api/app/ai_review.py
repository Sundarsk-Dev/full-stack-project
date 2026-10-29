"""F5 coordinator over the contract and ledger; transport is injected, never enabled here.

groq_transport.py bounds the live request with a killable child process. This
synchronous boundary also discards late replies without refunds or resends.
"""
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Protocol

from . import ai_ledger as ledger
from .ai_contract import MAX_REQUESTS, prepare_review, validate_response
from .analysis import analyze
from .scoring import compute_scores
from .db import utcnow
from .diagnostics import emit

CALL_TIMEOUT_SECONDS = 10.0
MAX_RETRY_WAIT_SECONDS = 2.0

MESSAGES = {
    "consent_required": "External review was not authorized. The result uses local rules.",
    "provider_unavailable": "External review is unavailable. The result uses local rules.",
    "unverified_quota": "External review limits are not verified. The result uses local rules.",
    "quota_exhausted": "External review capacity is unavailable. The result uses local rules.",
    "provider_rate_limited": "External review was rate limited. The result uses local rules.",
    "provider_outcome_unknown": "External review was interrupted. It was not automatically resent; the result uses local rules.",
    "usage_exceeded": "External review exceeded its reservation. The result uses local rules.",
    "analysis_changed": "The saved external review is incompatible with this analysis. The result uses local rules.",
}


@dataclass(frozen=True)
class ProviderReply:
    outcome: str  # received / rate_limited / failed / unknown
    text: str | None = None
    usage: tuple[int, int] | None = None
    retry_after_seconds: int = 0


class ReviewTransport(Protocol):
    def send(self, payload: dict, *, timeout_seconds: float) -> ProviderReply:
        """Send once with a total timeout, no internal retries and no tools."""
        ...


@dataclass(frozen=True)
class ReviewOutcome:
    result: dict
    baseline: dict
    requested_mode: str
    effective_mode: str
    status: str  # not_requested / not_needed / reviewed / limited / degraded
    degradation_reason: str | None
    degradation_message: str | None
    unresolved_count: int
    accepted_ai_findings: int
    provenance: tuple[dict, ...]


def _outcome(baseline, context, *, accepted=(), reasons=None, provenance=(), reviewed=False, degradation=None):
    result = deepcopy(baseline)
    reasons = reasons or {}
    # An operational failure returns the preserved baseline, including when an
    # earlier batch was usable. AI abstention by itself is not a provider failure.
    if degradation:
        accepted = ()
    clauses = {c["id"]: c for c in result["clauses"]}
    for finding in accepted:
        clause = clauses[finding["clause_id"]]
        clause["findings"].append({k: v for k, v in finding.items() if k != "clause_id"})
        clause["unrated_reason"] = None
    for clause in result["clauses"]:
        if clause["substantive"] and not clause["findings"]:
            clause["unrated_reason"] = reasons.get(clause["id"], degradation or clause["unrated_reason"])
    result["categories"], result["score"] = compute_scores(result["clauses"], context["document_type"])
    unresolved = sum(c["substantive"] and not c["findings"] for c in result["clauses"])
    if context["requested_mode"] == "rules_only": status = "not_requested"
    elif degradation: status = "degraded"
    elif not any(c["substantive"] and not c["findings"] for c in baseline["clauses"]): status = "not_needed"
    elif any(r in {"analysis_budget_excluded", "adequate_context_does_not_fit"} for r in reasons.values()): status = "limited"
    else: status = "reviewed"
    return ReviewOutcome(result, deepcopy(baseline), context["requested_mode"],
        "hybrid" if reviewed and not degradation else "rules_only", status, degradation,
        MESSAGES.get(degradation), unresolved, len(accepted), tuple(provenance))


def _blocked_reason(error):
    code = str(error)
    if code in {"job_lease_lost", "capture_unavailable", "attempt_unavailable"}:
        raise error  # Never turn lost ownership or a stale worker into a publishable report.
    return {
        "consent_required": "consent_required", "unverified_quota": "unverified_quota",
        "quota_exhausted": "quota_exhausted", "quota_configuration_changed": "unverified_quota",
        "context_changed": "analysis_changed", "batch_changed": "analysis_changed",
        "batch_already_attempted": "analysis_changed", "previous_attempt_unresolved": "provider_outcome_unknown",
        "dispatch_not_allowed": "provider_outcome_unknown", "attempt_already_finished": "provider_outcome_unknown",
        "retry_not_allowed": "provider_rate_limited", "retry_not_due": "provider_rate_limited",
    }.get(code, "provider_unavailable")


def _dispatch(factory, job_id, token, call_id, transport):
    permit = ledger.begin_dispatch(factory, job_id, token, call_id)
    emit("ai_dispatch_started", job_id=job_id, call_id=call_id)
    timeout = min(CALL_TIMEOUT_SECONDS, (permit.deadline - utcnow()).total_seconds())
    reply = ProviderReply("unknown")
    if timeout > 0:
        started = monotonic()
        try:
            reply = transport.send(deepcopy(permit.payload), timeout_seconds=timeout)
        except Exception:
            # Raw exceptions can contain policy text, provider keys or URLs.
            reply = ProviderReply("unknown")
        if monotonic() - started >= timeout or utcnow() >= permit.deadline:
            reply = ProviderReply("unknown")
    if not isinstance(reply, ProviderReply) or reply.outcome not in {"received", "rate_limited", "failed", "unknown"}:
        reply = ProviderReply("unknown")
    if (reply.outcome == "rate_limited" and (type(reply.retry_after_seconds) is not int
            or not 0 <= reply.retry_after_seconds <= 60)):
        reply = ProviderReply("failed")  # Do not shorten an unsupported Retry-After and retry early.
    state = ledger.finish(factory, job_id, token, call_id, outcome=reply.outcome, response_text=reply.text, usage=reply.usage,
                          retry_after_seconds=reply.retry_after_seconds if reply.outcome == "rate_limited" else 0)
    emit("ai_dispatch_finished", job_id=job_id, call_id=call_id, state=state)


def review_job(factory, job_id, token, *, policy=None, transport: ReviewTransport | None = None):
    """Use only the persisted job/capture, consent and attempts; never caller text/mode."""
    context = ledger.review_context(factory, job_id, token)
    baseline = analyze(context["text"], context["document_type"], extractor=context["extractor"], headings=context["headings"])
    if context["requested_mode"] == "rules_only": return _outcome(baseline, context)
    if context["requested_mode"] != "hybrid" or not context["consent"]:
        return _outcome(baseline, context, degradation="consent_required")
    if not any(c["substantive"] and not c["findings"] for c in baseline["clauses"]):
        return _outcome(baseline, context)
    accepted, reasons, provenance, reviewed = [], {}, [], False

    def result(degradation=None):
        outcome = _outcome(baseline, context, accepted=accepted, reasons=reasons, provenance=provenance,
                           reviewed=reviewed, degradation=degradation)
        emit("ai_review_finished", job_id=job_id, state=outcome.status, error_code=degradation)
        return outcome

    try:
        attempts = ledger.recover(factory, job_id, token, analysis=baseline, policy=policy)
        model = policy.model if policy else attempts[0]["model"] if attempts else None
        if model is None: return result("unverified_quota")
        try:
            batches, reasons = prepare_review(baseline, model=model, consent=True)
        except ValueError:
            return result("provider_unavailable")
        hashes = {ledger.digest(batch.payload) for batch in batches}
        if any(a["batch_hash"] not in hashes for a in attempts): return result("analysis_changed")
        for batch in batches:
            batch_hash = ledger.digest(batch.payload)
            # Two possible sends plus one final read of their persisted result.
            for _ in range(MAX_REQUESTS + 1):
                attempts = ledger.recover(factory, job_id, token, analysis=baseline, policy=policy)
                prior = next((a for a in reversed(attempts) if a["batch_hash"] == batch_hash), None)
                if prior and prior["state"] == "received":
                    saved_batch, raw = ledger.saved_response(factory, job_id, token, prior["id"], baseline)
                    findings, abstentions = validate_response(raw, saved_batch, baseline)
                    accepted.extend(findings)
                    reasons.update(abstentions)
                    provenance.append({k: prior[k] for k in ("id", "provider", "model", "prompt_revision", "snapshot", "clause_ids")})
                    reviewed = True
                    break
                if prior and prior["state"] in {"reserved", "sending", "unknown"}:
                    return result("provider_outcome_unknown")
                if prior and prior["state"] == "failed":
                    return result("usage_exceeded" if prior["error_code"] == "usage_exceeded" else "provider_unavailable")
                retry_of = None
                if prior and prior["state"] == "retryable":
                    if len(attempts) >= MAX_REQUESTS or prior["retry_of"]:
                        return result("provider_rate_limited")
                    wait = max(0, (prior["retry_after"] - utcnow()).total_seconds()) if prior["retry_after"] else 0
                    if wait > MAX_RETRY_WAIT_SECONDS: return result("provider_rate_limited")
                    retry_of = prior["id"]
                elif len(attempts) >= MAX_REQUESTS:
                    reasons.update({i: "analysis_budget_excluded" for i in batch.clause_ids})
                    break
                if policy is None: return result("unverified_quota")
                if transport is None: return result("provider_unavailable")
                if retry_of and wait: sleep(wait)
                try:
                    call_id = ledger.reserve(factory, job_id, token, baseline, batch, policy=policy, retry_of=retry_of)
                except ledger.LedgerBlocked as error:
                    if str(error) != "analysis_budget_exhausted": raise
                    reasons.update({i: "analysis_budget_excluded" for i in batch.clause_ids})
                    break
                _dispatch(factory, job_id, token, call_id, transport)
        return result()
    except ledger.LedgerBlocked as error:
        return result(_blocked_reason(error))
