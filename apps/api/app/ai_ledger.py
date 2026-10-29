"""Durable F5 reservations and dispatch states; transport supplied by the coordinator.

Quota rows use conservative quiet-period windows: all usage stays charged until
the latest dispatch deadline plus the period. This bounds rolling/fixed windows
without assuming the provider's reset clock. It may underuse verified capacity.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .ai_contract import PROMPT_REVISION, ReviewBatch, prepare_review, snapshot
from .db import utcnow
from .models import AIBudgetWindow, AICall, Document, DocumentVersion, Job, new_id

RESOURCES = {"requests", "input_tokens", "output_tokens", "total_tokens"}


class LedgerBlocked(Exception):
    """Fixed reason code only; callers display an explicit rules-only degradation."""


@dataclass(frozen=True)
class QuotaLimit:
    name: str  # Stable account/model quota identifier, never a key or user input.
    resource: str
    period_seconds: int
    capacity: int


@dataclass(frozen=True)
class QuotaPolicy:
    account_id: str  # SHA-256 of a stable, nonsecret account identifier; not an API key.
    model: str
    valid_until: datetime
    limits: tuple[QuotaLimit, ...]
    provider: str = "groq"


@dataclass(frozen=True)
class DispatchPermit:
    payload: dict
    deadline: datetime


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_policy(policy, now):
    if policy is None or not policy.limits or policy.valid_until <= now:
        raise LedgerBlocked("unverified_quota")
    if policy.provider != "groq" or not re.fullmatch(r"[a-f0-9]{64}", policy.account_id):
        raise LedgerBlocked("unverified_quota")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.:-]{0,127}", policy.model):
        raise LedgerBlocked("unverified_model")
    resources, names = set(), set()
    for limit in policy.limits:
        if (not re.fullmatch(r"[A-Za-z0-9_:.-]{1,80}", limit.name) or limit.name in names
                or limit.resource not in RESOURCES or type(limit.capacity) is not int
                or not 0 < limit.capacity <= 1_000_000_000 or type(limit.period_seconds) is not int
                or not 1 <= limit.period_seconds <= 31 * 86400):
            raise LedgerBlocked("unverified_quota")
        resources.add(limit.resource)
        names.add(limit.name)
    if len(names) > 16 or "requests" not in resources or not (
            "total_tokens" in resources or {"input_tokens", "output_tokens"} <= resources):
        raise LedgerBlocked("unverified_quota")


def _job(db, job_id, token, *, require_consent=True):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (job is None or job.state != "running" or job.lease_token != token
            or not job.lease_expires_at or job.lease_expires_at <= utcnow()):
        raise LedgerBlocked("job_lease_lost")
    if require_consent and (job.requested_mode != "hybrid" or not job.consent
                            or job.consent_provider != "groq" or job.consent_at is None):
        raise LedgerBlocked("consent_required")
    version = db.get(DocumentVersion, job.version_id) if job.version_id else None
    document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update()) if version else None
    if document is None or document.tombstoned or document.owner_id != job.owner_id:
        raise LedgerBlocked("capture_unavailable")
    return job, version


def review_context(factory, job_id, token):
    """Load the owned immutable source; rules-only callers need no AI consent."""
    with factory.begin() as db:
        job, version = _job(db, job_id, token, require_consent=False)
        return {"text": version.text, "document_type": job.document_type,
                "extractor": version.extractor_version, "headings": (version.capture_metadata or {}).get("headings", []),
                "requested_mode": job.requested_mode,
                "consent": bool(job.consent and job.consent_provider == "groq" and job.consent_at is not None)}


def _identity(version_id, batch_snapshot, policy):
    return digest([version_id, batch_snapshot, policy.provider, policy.account_id, policy.model, PROMPT_REVISION])


def _cost(resource, input_tokens, output_tokens):
    return {"requests": 1, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens}[resource]


def _window(db, policy, limit, now):
    key = digest([policy.provider, policy.account_id, limit.name])
    window = db.scalar(select(AIBudgetWindow).where(AIBudgetWindow.id == key).with_for_update())
    if window is None:
        # The unique key resolves concurrent first use; the enclosing transaction
        # locks every quota in deterministic order, including after an insert race.
        try:
            with db.begin_nested():
                db.add(AIBudgetWindow(id=key, resource=limit.resource, period_seconds=limit.period_seconds,
                    capacity=limit.capacity, used=0, generation=new_id(), expires_at=now))
                db.flush()
        except IntegrityError:
            pass
        window = db.scalar(select(AIBudgetWindow).where(AIBudgetWindow.id == key).with_for_update())
    if window.expires_at <= now:
        window.used, window.generation = 0, new_id()
        window.resource, window.period_seconds, window.capacity = limit.resource, limit.period_seconds, limit.capacity
    elif window.resource != limit.resource or window.period_seconds != limit.period_seconds:
        raise LedgerBlocked("quota_configuration_changed")
    else:
        # Raising a limit does not replenish an active generation.
        window.capacity = min(window.capacity, limit.capacity)
    return window


def reserve(factory, job_id, token, analysis, batch, *, policy, retry_of=None):
    """Commit all capacity and the exact payload before returning an attempt ID."""
    now = utcnow()
    validate_policy(policy, now)
    if batch not in prepare_review(analysis, model=policy.model, consent=True)[0]:
        raise LedgerBlocked("batch_changed")
    with factory.begin() as db:
        job, version = _job(db, job_id, token)
        if analysis["text"] != version.text:
            raise LedgerBlocked("context_changed")
        identity = _identity(version.id, batch.snapshot, policy)
        batch_hash = digest(batch.payload)
        calls = list(db.scalars(select(AICall).where(AICall.job_id == job.id).order_by(AICall.attempt_number)))
        if any(c.identity != identity for c in calls):
            raise LedgerBlocked("context_changed")
        if any(c.state in {"reserved", "sending", "unknown"} or c.error_code == "usage_exceeded" for c in calls):
            raise LedgerBlocked("previous_attempt_unresolved")
        if (len(calls) >= 2 or sum(c.input_reserved for c in calls) + batch.input_reservation > 10000
                or sum(c.output_reserved for c in calls) + batch.output_reservation > 2000):
            raise LedgerBlocked("analysis_budget_exhausted")
        prior = next((c for c in calls if c.id == retry_of), None)
        if retry_of is not None:
            if (prior is None or prior.state != "retryable" or prior.retry_of is not None
                    or prior.batch_hash != batch_hash or any(c.retry_of for c in calls)):
                raise LedgerBlocked("retry_not_allowed")
            if prior.retry_after and prior.retry_after > now:
                raise LedgerBlocked("retry_not_due")
        elif any(set(c.clause_ids) & set(batch.clause_ids) for c in calls):
            raise LedgerBlocked("batch_already_attempted")
        if len(set(batch.clause_ids).union(*(set(c.clause_ids) for c in calls))) > 12:
            raise LedgerBlocked("analysis_budget_exhausted")
        deadline = min(now + timedelta(seconds=15), job.lease_expires_at, policy.valid_until)
        charges = []
        ordered = sorted(policy.limits, key=lambda limit: digest([policy.provider, policy.account_id, limit.name]))
        for limit in ordered:
            window = _window(db, policy, limit, now)
            cost = _cost(limit.resource, batch.input_reservation, batch.output_reservation)
            if window.used + cost > window.capacity:
                raise LedgerBlocked("quota_exhausted")
            window.used += cost
            window.expires_at = max(window.expires_at, deadline + timedelta(seconds=limit.period_seconds))
            charges.append({"id": window.id, "generation": window.generation, "resource": limit.resource, "amount": cost})
        call = AICall(job_id=job.id, version_id=version.id, attempt_number=len(calls) + 1, retry_of=retry_of,
            identity=identity, batch_hash=batch_hash, snapshot=batch.snapshot, provider=policy.provider,
            model=policy.model, prompt_revision=PROMPT_REVISION, clause_ids=list(batch.clause_ids),
            request_payload=batch.payload, input_reserved=batch.input_reservation, output_reserved=batch.output_reservation,
            budget_charges=charges, lease_token=token, dispatch_before=deadline)
        db.add(call)
        db.flush()
        call_id = call.id
    return call_id


def _call(db, job_id, call_id):
    call = db.scalar(select(AICall).where(AICall.id == call_id, AICall.job_id == job_id).with_for_update())
    if call is None:
        raise LedgerBlocked("attempt_unavailable")
    return call


def begin_dispatch(factory, job_id, token, call_id):
    """Commit sending once; the transport must honor the returned deadline."""
    with factory.begin() as db:
        _job(db, job_id, token)
        call = _call(db, job_id, call_id)
        if call.state != "reserved" or call.lease_token != token or call.dispatch_before <= utcnow():
            raise LedgerBlocked("dispatch_not_allowed")
        if digest(call.request_payload) != call.batch_hash:
            raise LedgerBlocked("batch_changed")
        call.state = "sending"
        permit = DispatchPermit(call.request_payload, call.dispatch_before)
    return permit


def finish(factory, job_id, token, call_id, *, outcome, response_text=None, usage=None, retry_after_seconds=0):
    """Persist before interpreting. Only an explicit rate-limit rejection is retryable.

    The adapter supplies provider usage; absent/invalid usage retains the
    full reservation. Response text is private data, never diagnostic content.
    """
    if outcome not in {"received", "rate_limited", "failed", "unknown"}:
        raise ValueError("Unknown outcome")
    if type(retry_after_seconds) is not int or not 0 <= retry_after_seconds <= 60:
        raise ValueError("Retry-After exceeds the bounded retry wait")
    if outcome == "received" and (not isinstance(response_text, str) or len(response_text.encode("utf-8")) > 65536):
        outcome, response_text = "failed", None
    valid_usage = (isinstance(usage, tuple) and len(usage) == 2
                   and all(type(n) is int and 0 <= n <= 1_000_000 for n in usage))
    with factory.begin() as db:
        _job(db, job_id, token)
        call = _call(db, job_id, call_id)
        if call.state != "sending" or call.lease_token != token:
            raise LedgerBlocked("attempt_already_finished")
        call.state = {"received": "received", "rate_limited": "retryable", "failed": "failed", "unknown": "unknown"}[outcome]
        call.error_code = None if outcome == "received" else outcome
        call.response_text = response_text if outcome == "received" else None
        call.finished_at = utcnow()
        if outcome == "rate_limited": call.retry_after = utcnow() + timedelta(seconds=retry_after_seconds)
        # Unknown outcomes cannot earn a refund, even if the caller supplies usage.
        if valid_usage and outcome != "unknown":
            call.input_tokens, call.output_tokens = usage
            for charge in sorted(call.budget_charges, key=lambda c: c["id"]):
                window = db.scalar(select(AIBudgetWindow).where(AIBudgetWindow.id == charge["id"]).with_for_update())
                if window and window.generation == charge["generation"]:
                    window.used += _cost(charge["resource"], *usage) - charge["amount"]
            if usage[0] > call.input_reserved or usage[1] > call.output_reserved:
                call.state, call.error_code = "failed", "usage_exceeded"
        state = call.state
    return state


def recover(factory, job_id, token, *, analysis=None, policy=None):
    """Fence interrupted attempts; keep their capacity and never return a payload."""
    with factory.begin() as db:
        _, version = _job(db, job_id, token)
        if analysis is not None and analysis["text"] != version.text:
            raise LedgerBlocked("context_changed")
        fingerprint = snapshot(analysis) if analysis is not None else None
        calls = db.scalars(select(AICall).where(AICall.job_id == job_id).order_by(AICall.attempt_number))
        states = []
        for call in calls:
            if fingerprint is not None and (call.version_id != version.id or call.snapshot != fingerprint
                    or call.prompt_revision != PROMPT_REVISION or digest(call.request_payload) != call.batch_hash
                    or (policy is not None and call.identity != _identity(version.id, fingerprint, policy))):
                raise LedgerBlocked("context_changed")
            if call.state in {"reserved", "sending"} and (call.lease_token != token or call.dispatch_before <= utcnow()):
                call.state, call.error_code, call.finished_at = "unknown", "interrupted", utcnow()
            states.append({"id": call.id, "state": call.state, "attempt": call.attempt_number,
                "batch_hash": call.batch_hash, "clause_ids": list(call.clause_ids), "provider": call.provider,
                "model": call.model, "prompt_revision": call.prompt_revision, "snapshot": call.snapshot,
                "retry_of": call.retry_of, "retry_after": call.retry_after, "error_code": call.error_code})
    return states


def saved_response(factory, job_id, token, call_id, analysis):
    with factory.begin() as db:
        _, version = _job(db, job_id, token)
        call = _call(db, job_id, call_id)
        if (call.version_id != version.id or analysis["text"] != version.text or call.snapshot != snapshot(analysis)
                or call.prompt_revision != PROMPT_REVISION or digest(call.request_payload) != call.batch_hash):
            raise LedgerBlocked("context_changed")
        if call.state != "received":
            raise LedgerBlocked("response_unavailable")
        batch = ReviewBatch(tuple(call.clause_ids), call.snapshot, call.request_payload, call.input_reserved, call.output_reserved)
        return batch, call.response_text
