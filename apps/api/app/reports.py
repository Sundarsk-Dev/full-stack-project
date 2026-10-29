from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select

from .scoring import CATEGORY_NAMES, saved_profiles
from .ai_review import MESSAGES
from .db import timestamp
from .models import Analysis, CategoryScore, Clause, ClauseFinding, Document, DocumentVersion


def owned_analysis(db, owner_id, analysis_id):
    row = db.execute(select(Analysis, DocumentVersion, Document)
        .join(DocumentVersion, Analysis.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(Analysis.id == analysis_id, Analysis.owner_id == owner_id,
               Document.owner_id == owner_id, Document.tombstoned.is_(False))).first()
    if row is None:
        raise HTTPException(404, "Report not found.")
    return row


def finding_data(f):
    return {key: getattr(f, key) for key in ("category", "rating", "severity", "quote", "start", "end", "explanation", "source", "rule_id", "ai_call_id")}


def report_data(db, owner_id, analysis_id):
    a, version, document = owned_analysis(db, owner_id, analysis_id)
    clauses = db.scalars(select(Clause).where(Clause.analysis_id == a.id).order_by(Clause.local_id)).all()
    findings = db.scalars(select(ClauseFinding).join(Clause).where(Clause.analysis_id == a.id)
                          .order_by(Clause.local_id, ClauseFinding.category, ClauseFinding.id)).all()
    by_clause = defaultdict(list)
    for f in findings:
        by_clause[f.clause_id].append(finding_data(f))
    categories = db.scalars(select(CategoryScore).where(CategoryScore.analysis_id == a.id).order_by(CategoryScore.category)).all()
    baseline = None
    if a.rules_baseline:
        baseline = {**a.rules_baseline, "clauses": [{**c, "text": version.text[c["start"]:c["end"]]} for c in a.rules_baseline["clauses"]]}
    report = {"id": a.id, "document_id": document.id, "version_id": version.id, "title": document.title,
        "version_number": version.version_number, "revision_number": a.revision_number,
        "document_type": document.document_type, "created_at": timestamp(a.created_at), "captured_at": timestamp(version.captured_at),
        "source_kind": document.source_kind, "capture": version.capture_metadata,
        "requested_mode": a.requested_mode, "effective_mode": a.effective_mode, "engine": a.engine,
        "consent_provider": a.consent_provider, "consent_at": timestamp(a.consent_at),
        "review_status": a.review_status, "degradation_reason": a.degradation_reason,
        "degradation_message": MESSAGES.get(a.degradation_reason), "rules_baseline": baseline,
        "unresolved_count": a.unresolved_count if a.unresolved_count is not None else sum(c.substantive and not by_clause[c.id] for c in clauses),
        "accepted_ai_findings": a.accepted_ai_findings or 0, "ai_provenance": a.ai_provenance or [],
        "text": version.text, "supported_groups": a.supported_groups,
        "score": {"coverage": a.coverage, "best": a.best, "worst": a.worst, "grade": a.grade, "scope_consistent": a.scope_consistent},
        "clauses": [{"id": c.local_id, "start": c.start, "end": c.end, "text": version.text[c.start:c.end],
                     "substantive": c.substantive, "unrated_reason": c.unrated_reason, "findings": by_clause[c.id]} for c in clauses],
        "categories": [{"id": c.category, "name": CATEGORY_NAMES[c.category - 1], "applicable": c.applicable,
                        "max_severity": c.max_severity, "unknown": c.unknown, "accepted_count": c.accepted_count} for c in categories]}
    report["score_profiles"] = saved_profiles(report, document.document_type)
    if baseline:
        baseline["score_profiles"] = saved_profiles(baseline, document.document_type)
    return report
