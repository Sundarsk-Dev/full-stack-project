"""Allowlisted diagnostic metadata; never serialize input, credentials or exceptions."""
from importlib import metadata
import json
import logging
import re
import sys
from uuid import UUID

from sqlalchemy import func, select

from .db import timestamp, utcnow
from .models import AICall, Analysis, Clause, ClauseFinding, DocumentVersion, Job
from .url_guard import ERROR_CODES

LOG = logging.getLogger("termslens.events")


def configure_logging():
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    if not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOG.addHandler(handler)


ERRORS = {
    **{code: "The public page could not be captured. Paste the policy text to continue." for code in ERROR_CODES},
    "invalid_input": "The policy could not be processed. Check its format and size.",
    "capture_unavailable": "The source document is unavailable.",
    "evidence_mismatch": "Evidence could not be matched to its saved source.",
    "worker_error": "Processing was interrupted. The worker will retry within its attempt limit.",
    "attempts_exhausted": "Processing stopped after three attempts. Submit again to retry.",
}


def emit(event, *, request_id=None, job_id=None, call_id=None, stage=None, state=None, error_code=None, error_type=None, status=None):
    record = {"event": event, "request_id": request_id, "job_id": job_id, "call_id": call_id, "stage": stage,
              "state": state, "error_code": error_code, "error_type": error_type, "status": status}
    for name, value in record.items():
        if value is None: continue
        if name in {"request_id", "job_id", "call_id"}:
            UUID(str(value))
        elif name == "status":
            if type(value) is not int or not 100 <= value <= 599: raise ValueError("Invalid diagnostic status")
        elif not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", value):
            raise ValueError("Diagnostic labels must be fixed identifiers")
    LOG.info(json.dumps({k: v for k, v in record.items() if v is not None}, sort_keys=True))


def input_error_code(error):
    # Only known fixed messages affect the public error. Do not forward str(error).
    if str(error) == "The source document is unavailable.": return "capture_unavailable"
    if str(error) in {"Evidence could not be matched to the source.", "The analysis did not preserve its source text."}:
        return "evidence_mismatch"
    return "invalid_input"


def runtime_report(settings):
    from .groq_config import groq_readiness

    versions = {}
    for name in ["fastapi", "pydantic", "SQLAlchemy", "alembic", "PyMySQL", "argon2-cffi", "uvicorn", "httpx"]:
        try: versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError: versions[name] = "missing"
    return {"python": sys.version.split()[0], "dependencies": versions,
            "database_configured": bool(settings.database_url), "allowed_origin_count": len(settings.allowed_origins),
            "secure_cookies": settings.secure_cookies, "external_review": groq_readiness(settings).public_status(),
            "network_or_provider_calls": False}


def inspect_job(db, job_id):
    """Local CLI only: no public diagnostic/admin route and no mutations."""
    job = db.get(Job, str(UUID(job_id)))
    if job is None: return None
    version = db.get(DocumentVersion, job.version_id) if job.version_id else None
    analysis = db.get(Analysis, job.result_id) if job.result_id else None
    result = {"job_id": job.id, "request_id": job.request_id, "state": job.state, "stage": job.stage,
              "attempts": job.attempts, "lease_active": bool(job.lease_expires_at and job.lease_expires_at > utcnow()),
              "lease_expires_at": timestamp(job.lease_expires_at), "version_id": job.version_id, "result_id": job.result_id,
              "last_error_code": job.error_code, "last_error_stage": job.error_stage,
              "requested_mode": job.requested_mode, "created_at": timestamp(job.created_at), "finished_at": timestamp(job.finished_at)}
    result.update(operation=job.operation, checked_at=timestamp(job.checked_at), capture_changed=job.capture_changed, result_reused=job.result_reused)
    if version: result["capture"] = {"hash": version.content_hash, "extractor": version.extractor_version}
    result["ai_attempts"] = [{"id": c.id, "state": c.state, "attempt": c.attempt_number, "error_code": c.error_code}
        for c in db.scalars(select(AICall).where(AICall.job_id == job.id).order_by(AICall.attempt_number))]
    if analysis:
        result["analysis"] = {"engine": analysis.engine, "effective_mode": analysis.effective_mode,
            "review_status": analysis.review_status, "degradation_reason": analysis.degradation_reason,
            "clauses": db.scalar(select(func.count()).select_from(Clause).where(Clause.analysis_id == analysis.id)),
            "findings": db.scalar(select(func.count()).select_from(ClauseFinding).join(Clause).where(Clause.analysis_id == analysis.id))}
    return result
