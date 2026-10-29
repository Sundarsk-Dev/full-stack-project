"""Owned version reads and compatibility for immutable report reuse."""
from fastapi import HTTPException
from sqlalchemy import func, select

from .ai_contract import PROMPT_REVISION
from .db import timestamp
from .models import Analysis, Document, DocumentVersion, Job
from .publication import baseline_snapshot
from .scoring import PROFILE_REVISION


def owned_document(db, owner_id, document_id, *, lock=False):
    query = select(Document).where(Document.id == document_id, Document.owner_id == owner_id, Document.tombstoned.is_(False))
    document = db.scalar(query.with_for_update() if lock else query)
    if document is None: raise HTTPException(404, "Document not found.")
    return document


def latest_version(db, document_id):
    return db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document_id)
                     .order_by(DocumentVersion.version_number.desc()).limit(1))


def same_capture(version, text, extractor, headings):
    return (version is not None and version.text == text and version.extractor_version == extractor
            and (version.capture_metadata or {}).get("headings", []) == headings)


def reusable(analysis, job, baseline, model):
    if (analysis is None or analysis.owner_id != job.owner_id or analysis.version_id != job.version_id
            or analysis.profile_revision != PROFILE_REVISION or analysis.requested_mode != job.requested_mode
            or analysis.engine != baseline["engine"] or analysis.rules_baseline != baseline_snapshot(baseline)
            or analysis.degradation_reason):
        return False
    if job.requested_mode == "rules_only":
        return analysis.effective_mode == "rules_only" and analysis.review_status == "not_requested"
    if not job.consent or job.consent_provider != "groq" or job.consent_at is None:
        return False
    if analysis.review_status == "not_needed":
        return analysis.effective_mode == "rules_only" and not analysis.ai_provenance
    return (analysis.effective_mode == "hybrid" and analysis.review_status in {"reviewed", "limited"}
            and bool(model and analysis.ai_provenance)
            and all(p["provider"] == "groq" and p["model"] == model and p["prompt_revision"] == PROMPT_REVISION
                    for p in analysis.ai_provenance))


def version_history(db, owner_id, document_id, limit, offset):
    document = owned_document(db, owner_id, document_id)
    query = select(DocumentVersion.id, DocumentVersion.version_number, DocumentVersion.parent_id,
        DocumentVersion.captured_at, DocumentVersion.content_hash, DocumentVersion.extractor_version).where(DocumentVersion.document_id == document_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    versions = db.execute(query.order_by(DocumentVersion.version_number.desc()).offset(offset).limit(limit)).all()
    # Return one latest report per capture; revisions have their own bounded endpoint.
    stats = select(Analysis.version_id, func.max(Analysis.revision_number).label("revision"), func.count().label("report_count")).where(
        Analysis.version_id.in_([v.id for v in versions]), Analysis.owner_id == owner_id).group_by(Analysis.version_id).subquery()
    reports = {r.version_id: r for r in db.execute(select(Analysis.id, Analysis.version_id, stats.c.report_count).join(stats,
        (Analysis.version_id == stats.c.version_id) & (Analysis.revision_number == stats.c.revision))).all()}
    items = []
    for version in versions:
        report = reports.get(version.id)
        items.append({"id": version.id, "version_number": version.version_number, "parent_id": version.parent_id,
            "captured_at": timestamp(version.captured_at), "content_hash": version.content_hash,
            "extractor": version.extractor_version, "analysis_count": report.report_count if report else 0, "latest_analysis_id": report.id if report else None})
    check = db.execute(select(Job.id, Job.checked_at, Job.capture_changed, Job.result_reused).where(
        Job.document_id == document_id, Job.owner_id == owner_id, Job.checked_at.is_not(None))
        .order_by(Job.checked_at.desc(), Job.id.desc()).limit(1)).first()
    return {"document_id": document.id, "title": document.title, "source_kind": document.source_kind,
        "document_type": document.document_type, "items": items, "total": total, "limit": limit, "offset": offset,
        "last_check": {"checked_at": timestamp(check.checked_at), "changed": check.capture_changed,
                       "reused": check.result_reused, "job_id": check.id} if check else None}


def revision_history(db, owner_id, document_id, version_id, limit, offset):
    owned_document(db, owner_id, document_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != document_id: raise HTTPException(404, "Capture not found.")
    query = select(Analysis.id, Analysis.revision_number, Analysis.created_at, Analysis.requested_mode,
        Analysis.effective_mode, Analysis.degradation_reason).where(Analysis.version_id == version_id, Analysis.owner_id == owner_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Analysis.revision_number.desc()).offset(offset).limit(limit)).all()
    return {"items": [{"id": a.id, "revision_number": a.revision_number, "created_at": timestamp(a.created_at),
            "requested_mode": a.requested_mode, "effective_mode": a.effective_mode, "degradation_reason": a.degradation_reason} for a in rows],
            "total": total, "limit": limit, "offset": offset}
