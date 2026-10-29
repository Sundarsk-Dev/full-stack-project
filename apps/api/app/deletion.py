"""Tombstone first, recover private-file cleanup, then atomically remove derived data."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from .db import timestamp, utcnow
from .diagnostics import emit
from .models import AICall, Analysis, CategoryScore, Clause, ClauseFinding, Comparison, Document, DocumentDeletion, DocumentVersion, Job, User


def deletion_data(row):
    return {"document_id": row.document_id, "state": row.state, "created_at": timestamp(row.created_at),
        "completed_at": timestamp(row.completed_at), "error_code": row.error_code}


def document_jobs(document_id):
    versions = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
    return or_(Job.document_id == document_id, Job.version_id.in_(versions))


def request_deletion(db, owner_id, document_id):
    # Same owner -> document ordering as enqueue; queued jobs also prevent deletion.
    db.execute(select(User.id).where(User.id == owner_id).with_for_update())
    old = db.scalar(select(DocumentDeletion).where(DocumentDeletion.document_id == document_id, DocumentDeletion.owner_id == owner_id))
    if old: return deletion_data(old)
    document = db.scalar(select(Document).where(Document.id == document_id, Document.owner_id == owner_id).with_for_update())
    if document is None: raise HTTPException(404, "Document not found.")
    if db.scalar(select(Job.id).where(document_jobs(document_id), Job.state.in_(["queued", "running"])).limit(1)):
        raise HTTPException(409, "Wait for this document's active jobs to finish before deleting it.")
    document.tombstoned = True
    row = DocumentDeletion(document_id=document_id, owner_id=owner_id)
    db.add(row); db.commit()
    return deletion_data(row)


def process_deletion(factory, snapshots):
    """One worker, up to 50 job folders per pass; failures remain pending across restart."""
    document_id = None
    try:
        with factory.begin() as db:
            row = db.scalar(select(DocumentDeletion).where(DocumentDeletion.state == "pending", DocumentDeletion.retry_at <= utcnow())
                            .order_by(DocumentDeletion.retry_at, DocumentDeletion.document_id).with_for_update(skip_locked=True).limit(1))
            if row is None: return False
            document_id, owner_id, cursor = row.document_id, row.owner_id, row.cursor_job_id
            row.retry_at = utcnow() + timedelta(seconds=10)
            document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            if not document or document.owner_id != owner_id or not document.tombstoned: raise ValueError()
            if db.scalar(select(Job.id).where(document_jobs(document_id), Job.state.in_(["queued", "running"])).limit(1)):
                raise ValueError()
            jobs = db.execute(select(Job.id, Job.owner_id, Job.request_hash).where(document_jobs(document_id)).order_by(Job.id)).all()
            if any(j.owner_id != owner_id for j in jobs): raise ValueError()
            expected = {f"{owner_id}/{j.id}/{j.request_hash}.json" for j in jobs}
            paths = db.scalars(select(DocumentVersion.snapshot_path).where(DocumentVersion.document_id == document_id,
                                                                          DocumentVersion.snapshot_path.is_not(None))).all()
            if any(path not in expected for path in paths): raise ValueError()
            batch = [j for j in jobs if cursor is None or j.id > cursor][:50]
        # Nothing holds a database row lock while waiting for filesystem locks/IO.
        for job in batch: snapshots.delete_job(owner_id, job.id, job.request_hash)
        with factory.begin() as db:
            row = db.scalar(select(DocumentDeletion).where(DocumentDeletion.document_id == document_id).with_for_update())
            document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            if row.state != "pending" or not document or not document.tombstoned or document.owner_id != owner_id:
                raise ValueError()
            if batch: row.cursor_job_id = batch[-1].id
            row.error_code, row.retry_at = None, utcnow()
            if any(j.id > (row.cursor_job_id or "") for j in jobs): return True
            versions = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
            analyses = select(Analysis.id).where(Analysis.version_id.in_(versions), Analysis.owner_id == owner_id)
            clauses = select(Clause.id).where(Clause.analysis_id.in_(analyses))
            job_ids = [j.id for j in jobs]
            db.execute(delete(Comparison).where(Comparison.owner_id == owner_id, or_(Comparison.left_id.in_(analyses), Comparison.right_id.in_(analyses))))
            db.execute(delete(ClauseFinding).where(ClauseFinding.clause_id.in_(clauses)))
            db.execute(delete(Clause).where(Clause.analysis_id.in_(analyses)))
            db.execute(delete(CategoryScore).where(CategoryScore.analysis_id.in_(analyses)))
            db.execute(update(AICall).where(AICall.job_id.in_(job_ids)).values(retry_of=None))
            db.execute(delete(AICall).where(AICall.job_id.in_(job_ids)))
            db.execute(delete(Job).where(Job.id.in_(job_ids)))
            db.execute(delete(Analysis).where(Analysis.version_id.in_(versions), Analysis.owner_id == owner_id))
            db.execute(update(DocumentVersion).where(DocumentVersion.document_id == document_id).values(parent_id=None))
            db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == document_id))
            db.execute(delete(Document).where(Document.id == document_id, Document.owner_id == owner_id))
            # Account-wide quota windows are deliberately not refunded or removed.
            row.state, row.completed_at, row.error_code = "complete", utcnow(), None
        emit("document_deleted")
        return True
    except (OSError, ValueError, SQLAlchemyError):
        if document_id:
            try:
                with factory.begin() as db:
                    row = db.get(DocumentDeletion, document_id)
                    if row and row.state == "pending":
                        row.error_code, row.retry_at = "cleanup_pending", utcnow() + timedelta(seconds=10)
            except SQLAlchemyError: pass
        emit("document_cleanup_pending", error_code="cleanup_pending")
        return False
