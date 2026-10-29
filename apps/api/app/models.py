from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db import utcnow


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


TIME = DateTime().with_variant(DATETIME(fsp=6), "mysql")
LONG = Text().with_variant(LONGTEXT(), "mysql")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[object] = mapped_column(TIME, index=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    document_type: Mapped[str] = mapped_column(String(16))
    source_kind: Mapped[str] = mapped_column(String(16), default="paste")
    tombstoned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number", name="uq_document_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    text: Mapped[str] = mapped_column(LONG)
    content_hash: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64), default="paste-v1")
    snapshot_path: Mapped[str | None] = mapped_column(String(512))
    capture_metadata: Mapped[dict | None] = mapped_column(JSON)
    captured_at: Mapped[object] = mapped_column(TIME, default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_owner_created", "owner_id", "created_at"),
                      UniqueConstraint("version_id", "revision_number", name="uq_analysis_revision"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    profile_revision: Mapped[str | None] = mapped_column(String(64))
    requested_mode: Mapped[str] = mapped_column(String(24))
    effective_mode: Mapped[str] = mapped_column(String(24))
    consent_provider: Mapped[str | None] = mapped_column(String(24))
    consent_at: Mapped[object | None] = mapped_column(TIME)
    review_status: Mapped[str | None] = mapped_column(String(24))
    degradation_reason: Mapped[str | None] = mapped_column(String(40))
    unresolved_count: Mapped[int | None] = mapped_column(Integer)
    accepted_ai_findings: Mapped[int | None] = mapped_column(Integer)
    rules_baseline: Mapped[dict | None] = mapped_column(JSON)
    ai_provenance: Mapped[list | None] = mapped_column(JSON)
    engine: Mapped[dict] = mapped_column(JSON)
    supported_groups: Mapped[list] = mapped_column(JSON)
    coverage: Mapped[float] = mapped_column(Float)
    best: Mapped[float] = mapped_column(Float)
    worst: Mapped[float] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(1))
    scope_consistent: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_owner_idempotency"),
        Index("ix_jobs_state_created", "state", "created_at"),
        Index("ix_jobs_state_lease", "state", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    input_text: Mapped[str] = mapped_column(LONG)
    input_url: Mapped[str | None] = mapped_column(String(2048))
    operation: Mapped[str] = mapped_column(String(16), default="new")
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    checked_at: Mapped[object | None] = mapped_column(TIME)
    check_metadata: Mapped[dict | None] = mapped_column(JSON)
    capture_changed: Mapped[bool | None] = mapped_column(Boolean)
    result_reused: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(160))
    document_type: Mapped[str] = mapped_column(String(16))
    requested_mode: Mapped[str] = mapped_column(String(24), default="rules_only")
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_provider: Mapped[str | None] = mapped_column(String(24))
    consent_at: Mapped[object | None] = mapped_column(TIME)
    state: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[object | None] = mapped_column(TIME)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    result_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id"), index=True)
    error: Mapped[str | None] = mapped_column(String(240))
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_stage: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)
    finished_at: Mapped[object | None] = mapped_column(TIME)


class Clause(Base):
    __tablename__ = "clauses"
    __table_args__ = (UniqueConstraint("analysis_id", "local_id", name="uq_analysis_clause"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    local_id: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    substantive: Mapped[bool] = mapped_column(Boolean)
    unrated_reason: Mapped[str | None] = mapped_column(String(240))


class ClauseFinding(Base):
    __tablename__ = "clause_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    clause_id: Mapped[str] = mapped_column(ForeignKey("clauses.id"), index=True)
    category: Mapped[int] = mapped_column(Integer)
    rating: Mapped[str] = mapped_column(String(16))
    severity: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(LONG)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24))
    rule_id: Mapped[str] = mapped_column(String(80))
    ai_call_id: Mapped[str | None] = mapped_column(ForeignKey("ai_calls.id"), index=True)


class CategoryScore(Base):
    __tablename__ = "category_scores"
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), primary_key=True)
    category: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_severity: Mapped[int] = mapped_column(Integer)
    unknown: Mapped[bool] = mapped_column(Boolean)
    applicable: Mapped[bool] = mapped_column(Boolean)
    accepted_count: Mapped[int] = mapped_column(Integer)


class FetchHost(Base):
    __tablename__ = "fetch_hosts"
    host: Mapped[str] = mapped_column(String(253), primary_key=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[object | None] = mapped_column(TIME)
    last_finished_at: Mapped[object | None] = mapped_column(TIME)
    interval_seconds: Mapped[float] = mapped_column(Float, default=1)


class Comparison(Base):
    __tablename__ = "comparisons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    left_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    right_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    profile: Mapped[str] = mapped_column(String(24))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)


class DocumentDeletion(Base):
    __tablename__ = "document_deletions"
    # No document FK: the minimal owned receipt survives removal of the document.
    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    cursor_job_id: Mapped[str | None] = mapped_column(String(36))
    retry_at: Mapped[object] = mapped_column(TIME, default=utcnow)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)
    completed_at: Mapped[object | None] = mapped_column(TIME)


class AIBudgetWindow(Base):
    __tablename__ = "ai_budget_windows"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource: Mapped[str] = mapped_column(String(24))
    period_seconds: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer)
    used: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[object] = mapped_column(TIME)


class AICall(Base):
    __tablename__ = "ai_calls"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number", name="uq_ai_job_attempt"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    retry_of: Mapped[str | None] = mapped_column(ForeignKey("ai_calls.id"))
    identity: Mapped[str] = mapped_column(String(64))
    batch_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(128))
    prompt_revision: Mapped[str] = mapped_column(String(64))
    clause_ids: Mapped[list] = mapped_column(JSON)
    request_payload: Mapped[dict] = mapped_column(JSON)
    input_reserved: Mapped[int] = mapped_column(Integer)
    output_reserved: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    budget_charges: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="reserved")
    lease_token: Mapped[str] = mapped_column(String(36))
    dispatch_before: Mapped[object] = mapped_column(TIME)
    retry_after: Mapped[object | None] = mapped_column(TIME)
    response_text: Mapped[str | None] = mapped_column(LONG)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[object] = mapped_column(TIME, default=utcnow)
    finished_at: Mapped[object | None] = mapped_column(TIME)
