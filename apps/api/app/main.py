from datetime import timedelta
from hashlib import sha256
import hmac
import json
import re
import secrets
from uuid import UUID, uuid4
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import delete, func, select, text as sql_text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from .config import ROOT, Settings
from .db import make_database, timestamp, utcnow
from .diagnostics import configure_logging, emit
from .groq_config import groq_readiness
from .models import Analysis, Clause, ClauseFinding, Document, DocumentDeletion, DocumentVersion, Job, LoginSession, User
from .reports import finding_data, owned_analysis, report_data
from .security import BodyLimitMiddleware, COOKIE, DUMMY_HASH, PASSWORDS, RateLimiter, csrf_token, token_hash, verify_password
from .url_guard import FetchError, validate_url
from .url_runtime import URLFetcher
from .versions import latest_version, owned_document, revision_history, version_history
from .comparisons import comparison_data, comparison_history, create_comparison
from .deletion import deletion_data, request_deletion


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_password_unicode(cls, value):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Password contains invalid Unicode.")
        return value


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Pasted policy", max_length=160)
    text: str | None = Field(default=None, min_length=1, max_length=200000)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    version_id: str | None = Field(default=None, min_length=36, max_length=36)
    document_type: Literal["terms", "privacy", "cookies", "refund"]
    mode: Literal["rules_only", "hybrid"] = "rules_only"
    consent: StrictBool = False
    provider: Literal["groq"] | None = None

    @model_validator(mode="after")
    def explicit_consent(self):
        if sum(value is not None for value in (self.text, self.url, self.version_id)) != 1:
            raise ValueError("Supply one of policy text, a public URL or an owned saved capture.")
        if self.mode == "hybrid" and (self.consent is not True or self.provider != "groq"):
            raise ValueError("Hybrid review requires explicit consent to send selected clauses and context to Groq.")
        if self.mode == "rules_only" and (self.consent or self.provider is not None):
            raise ValueError("Rules-only does not request external-review consent or a provider.")
        return self

    def request_hash(self):
        values = self.model_dump()
        if self.version_id is not None:
            values.pop("text")
            values.pop("url")
        elif self.url is None:
            values.pop("url")  # Existing paste and consent hashes remain identical.
        else:
            values.pop("text")
        if self.version_id is None: values.pop("version_id")
        if self.mode == "rules_only":
            # Preserve idempotency for requests queued before consent fields existed.
            values.pop("consent")
            values.pop("provider")
        return sha256(json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    @field_validator("version_id")
    @classmethod
    def capture_id(cls, value):
        return str(UUID(value)) if value is not None else None

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value):
        if value is None: return value
        if not value.strip() or len(value.split()) > 25000 or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Use nonempty Unicode text within 25,000 words.")
        return value

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        if value is None: return value
        try:
            return validate_url(value).url
        except FetchError:
            raise ValueError("Use a public HTTP(S) URL without credentials, on port 80 or 443.") from None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Title contains invalid Unicode.")
        return value.strip() or "Pasted policy"


class Discovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)

    _public_url = field_validator("url")(Submission.public_url.__func__)


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, min_length=1, max_length=200000)
    mode: Literal["rules_only", "hybrid"] = "rules_only"
    consent: StrictBool = False
    provider: Literal["groq"] | None = None

    _bounded_text = field_validator("text")(Submission.bounded_text.__func__)

    @model_validator(mode="after")
    def explicit_consent(self):
        if self.mode == "hybrid" and (not self.consent or self.provider != "groq"):
            raise ValueError("Hybrid review requires explicit consent to send selected clauses and context to Groq.")
        if self.mode == "rules_only" and (self.consent or self.provider is not None):
            raise ValueError("Rules-only does not request external-review consent or a provider.")
        return self

    def request_hash(self, document_id):
        values = {"operation": "refresh", "document_id": document_id, **self.model_dump()}
        return sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_id: str = Field(min_length=36, max_length=36)
    right_id: str = Field(min_length=36, max_length=36)
    kind: Literal["versions", "policies"]
    profile: Literal["balanced", "privacy_first"] = "balanced"
    _ids = field_validator("left_id", "right_id")(Submission.capture_id.__func__)


def create_app(settings=None, session_factory=None, *, url_fetcher=None):
    configure_logging()
    settings = settings or Settings.from_env()
    app = FastAPI(title="TermsLens", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(BodyLimitMiddleware)
    app.state.database_engine = None
    if session_factory is None and settings.database_url:
        try:
            app.state.database_engine, session_factory = make_database(settings.database_url, test=settings.test_mode)
        except (ValueError, ImportError):
            pass
    app.state.session_factory = session_factory
    limiter = RateLimiter()
    url_fetcher = url_fetcher or URLFetcher(settings.database_url, test=settings.test_mode)

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(_request, _exception):
        emit("api_database_error", request_id=getattr(_request.state, "request_id", None), error_type=type(_exception).__name__, status=503)
        return JSONResponse({"detail": "Database unavailable. Check local setup and migrations."}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exception):
        # Pydantic errors can include raw input (including passwords/policy bodies).
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exception.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    def database():
        if app.state.session_factory is None:
            raise HTTPException(503, "Database is not configured. Complete the README setup.")
        with app.state.session_factory() as db:
            yield db

    def check_origin(request: Request):
        if request.headers.get("origin") not in settings.allowed_origins:
            raise HTTPException(403, "Request origin is not allowed.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not allowed.")

    def throttle(request, action, limit):
        address = request.client.host if request.client else "unknown"
        if not limiter.allow((address, action), limit):
            raise HTTPException(429, "Too many requests. Try again in a minute.", headers={"Retry-After": "60"})

    def authenticated(request: Request, db=Depends(database)):
        token = request.cookies.get(COOKIE, "")
        if not 20 <= len(token) <= 128:
            raise HTTPException(401, "Please log in.")
        session = db.get(LoginSession, token_hash(token))
        if session is None or session.expires_at <= utcnow():
            raise HTTPException(401, "Your session has expired. Please log in.")
        user = db.get(User, session.user_id)
        if user is None:
            raise HTTPException(401, "Please log in.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token(token)):
                raise HTTPException(403, "Session verification failed. Reload and try again.")
        return user

    def start_session(db, user, request, response):
        old = request.cookies.get(COOKIE)
        if old:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old)))
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= utcnow()))
        token = secrets.token_urlsafe(32)
        db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(hours=settings.session_hours)))
        db.commit()
        response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True,
                            secure=settings.secure_cookies, samesite="strict", path="/api")
        response.headers["Cache-Control"] = "no-store"
        return {"user": {"id": user.id, "username": user.username}, "csrf_token": csrf_token(token)}

    @app.middleware("http")
    async def private_headers(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        emit("api_response", request_id=request.state.request_id, status=response.status_code)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health(db=Depends(database)):
        db.execute(sql_text("SELECT 1"))
        db.execute(select(Job.id).limit(1))
        return {"api": "ok", "database": "ready", "external_review": groq_readiness(settings).public_status()}

    @app.post("/api/auth/register", status_code=201)
    def register(credentials: Credentials, request: Request, response: Response, db=Depends(database)):
        check_origin(request)
        throttle(request, "register", 5)
        user = User(username=credentials.username.lower(), password_hash=PASSWORDS.hash(credentials.password))
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "That username is already registered.")
        return start_session(db, user, request, response)

    @app.post("/api/auth/login")
    def login(credentials: Credentials, request: Request, response: Response, db=Depends(database)):
        check_origin(request)
        throttle(request, "login", 10)
        user = db.scalar(select(User).where(User.username == credentials.username.lower()))
        valid = verify_password(user.password_hash if user else DUMMY_HASH, credentials.password)
        if user is None or not valid:
            raise HTTPException(401, "Invalid username or password.")
        return start_session(db, user, request, response)

    @app.get("/api/me")
    def me(request: Request, user=Depends(authenticated)):
        return {"user": {"id": user.id, "username": user.username}, "csrf_token": csrf_token(request.cookies[COOKIE])}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user=Depends(authenticated), db=Depends(database)):
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies[COOKIE]), LoginSession.user_id == user.id))
        db.commit()
        response.delete_cookie(COOKIE, path="/api", secure=settings.secure_cookies, httponly=True, samesite="strict")

    def enqueue(payload, request, idempotency_key, user, db, *, document_id=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", idempotency_key):
            raise HTTPException(422, "Use an alphanumeric idempotency key, with hyphens or underscores.")
        # Owner -> document locking serializes concurrent enqueue limits.
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        if document_id:
            digest = payload.request_hash(document_id)
            document = owned_document(db, user.id, document_id, lock=True)
            latest = latest_version(db, document.id)
            if latest is None: raise HTTPException(409, "The document has no saved capture.")
            if (document.source_kind == "paste") != (payload.text is not None):
                raise HTTPException(422, "Replace pasted documents with text; URL documents refresh their saved URL.")
            url = (latest.capture_metadata or {}).get("source_url") if document.source_kind == "url" else None
            if document.source_kind == "url" and not url: raise HTTPException(409, "The saved source URL is unavailable.")
            source = {"operation": "refresh", "document_id": document.id, "input_url": url,
                      "title": document.title, "document_type": document.document_type}
        else:
            digest = payload.request_hash()
            source = {"operation": "new", "input_url": payload.url, "title": payload.title, "document_type": payload.document_type}
            if payload.version_id:
                version = db.get(DocumentVersion, payload.version_id)
                if version is None: raise HTTPException(404, "Capture not found.")
                document = owned_document(db, user.id, version.document_id, lock=True)
                if payload.document_type != document.document_type: raise HTTPException(422, "Use the saved document's policy type.")
                source.update(operation="reanalysis", document_id=document.id, version_id=version.id, title=document.title)

        def existing_result():
            old = db.scalar(select(Job).where(Job.owner_id == user.id, Job.idempotency_key == idempotency_key))
            if old:
                if old.request_hash != digest:
                    raise HTTPException(409, "That submission key was already used for different input.")
                return {"job_id": old.id, "state": old.state}

        existing = existing_result()
        if existing:
            emit("job_reused", request_id=request.state.request_id, job_id=existing["job_id"])
            return existing
        throttle(request, "submit", 10)
        if not limiter.allow((user.id, "submit_owner"), 10):
            raise HTTPException(429, "Too many submissions. Try again in a minute.", headers={"Retry-After": "60"})
        active = db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.state.in_(["queued", "running"])))
        if active >= 5:
            raise HTTPException(429, "Wait for an existing analysis to finish before submitting more.")
        job = Job(owner_id=user.id, idempotency_key=idempotency_key, request_hash=digest, request_id=request.state.request_id,
                  input_text=payload.text or "", **source, requested_mode=payload.mode,
                  consent=payload.consent, consent_provider=payload.provider,
                  consent_at=utcnow() if payload.consent else None)
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = existing_result()
            if existing:
                return existing
            raise
        emit("job_queued", request_id=request.state.request_id, job_id=job.id, state=job.state)
        return {"job_id": job.id, "state": job.state}

    @app.post("/api/analyses", status_code=202)
    def submit(payload: Submission, request: Request, idempotency_key: str = Header(min_length=1, max_length=128),
               user=Depends(authenticated), db=Depends(database)):
        return enqueue(payload, request, idempotency_key, user, db)

    @app.post("/api/documents/{document_id}/refresh", status_code=202)
    def refresh(document_id: str, payload: Refresh, request: Request, idempotency_key: str = Header(min_length=1, max_length=128),
                user=Depends(authenticated), db=Depends(database)):
        return enqueue(payload, request, idempotency_key, user, db, document_id=document_id)

    @app.get("/api/documents/{document_id}/versions")
    def versions(document_id: str, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
                 user=Depends(authenticated), db=Depends(database)):
        return version_history(db, user.id, document_id, limit, offset)

    @app.get("/api/documents/{document_id}/versions/{version_id}/analyses")
    def revisions(document_id: str, version_id: str, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
                  user=Depends(authenticated), db=Depends(database)):
        return revision_history(db, user.id, document_id, version_id, limit, offset)

    @app.post("/api/discovery")
    def discover(payload: Discovery, request: Request, user=Depends(authenticated), db=Depends(database)):
        throttle(request, "discovery", 3)
        if not limiter.allow((user.id, "discovery_owner"), 3):
            raise HTTPException(429, "Too many discovery requests. Try again in a minute.", headers={"Retry-After": "60"})
        db.rollback()  # Authentication reads must not keep a transaction open during HTTP.
        try:
            return url_fetcher.fetch(payload.url, operation="discovery")
        except FetchError as error:
            raise HTTPException(422, f"Discovery could not read this page ({error}). Paste the policy text to continue.") from None

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, user=Depends(authenticated), db=Depends(database)):
        job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == user.id))
        if job is None:
            raise HTTPException(404, "Job not found.")
        if job.document_id:
            owned_document(db, user.id, job.document_id)
        return {"id": job.id, "state": job.state, "stage": job.stage, "attempts": job.attempts,
                "result_id": job.result_id, "error": job.error, "created_at": timestamp(job.created_at),
                "request_id": job.request_id, "error_code": job.error_code, "error_stage": job.error_stage,
                "requested_mode": job.requested_mode, "operation": job.operation,
                "document_id": job.document_id, "version_id": job.version_id,
                "checked_at": timestamp(job.checked_at), "capture_changed": job.capture_changed, "result_reused": job.result_reused}

    @app.get("/api/analyses")
    def history(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000), user=Depends(authenticated), db=Depends(database)):
        query = select(Analysis, DocumentVersion, Document).join(DocumentVersion, Analysis.version_id == DocumentVersion.id).join(Document, DocumentVersion.document_id == Document.id)
        query = query.where(Analysis.owner_id == user.id, Document.owner_id == user.id, Document.tombstoned.is_(False))
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.execute(query.order_by(Analysis.created_at.desc(), Analysis.id.desc()).offset(offset).limit(limit)).all()
        return {"items": [{"id": a.id, "title": d.title, "document_type": d.document_type, "created_at": timestamp(a.created_at),
                "document_id": d.id, "version_id": v.id, "version_number": v.version_number, "revision_number": a.revision_number,
                "requested_mode": a.requested_mode, "effective_mode": a.effective_mode, "review_status": a.review_status,
                "degradation_reason": a.degradation_reason, "coverage": a.coverage, "grade": a.grade, "best": a.best, "worst": a.worst} for a, v, d in rows],
                "total": total, "limit": limit, "offset": offset}

    @app.get("/api/analyses/{analysis_id}")
    def report(analysis_id: str, user=Depends(authenticated), db=Depends(database)):
        return report_data(db, user.id, analysis_id)

    @app.get("/api/analyses/{analysis_id}/findings")
    def findings(analysis_id: str, category: int | None = Query(None, ge=1, le=8), rating: Literal["favorable", "neutral", "concerning", "invasive"] | None = None,
                 source: Literal["rule", "library_exact", "ai"] | None = None, limit: int = Query(20, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=10000), user=Depends(authenticated), db=Depends(database)):
        owned_analysis(db, user.id, analysis_id)
        query = select(ClauseFinding).join(Clause).where(Clause.analysis_id == analysis_id)
        if category is not None: query = query.where(ClauseFinding.category == category)
        if rating is not None: query = query.where(ClauseFinding.rating == rating)
        if source is not None: query = query.where(ClauseFinding.source == source)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(Clause.local_id, ClauseFinding.id).offset(offset).limit(limit)).all()
        return {"items": [finding_data(f) for f in rows], "total": total, "limit": limit, "offset": offset}

    @app.post("/api/comparisons", status_code=201)
    def compare(payload: ComparisonInput, request: Request, user=Depends(authenticated), db=Depends(database)):
        throttle(request, "compare", 10)
        if not limiter.allow((user.id, "compare_owner"), 10): raise HTTPException(429, "Too many comparisons. Try again in a minute.")
        return create_comparison(db, user.id, payload.left_id, payload.right_id, payload.kind, payload.profile)

    @app.get("/api/comparisons")
    def comparisons(analysis_id: str, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
                    user=Depends(authenticated), db=Depends(database)):
        return comparison_history(db, user.id, analysis_id, limit, offset)

    @app.get("/api/comparisons/{comparison_id}")
    def comparison(comparison_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                   changes_only: bool = False, user=Depends(authenticated), db=Depends(database)):
        return comparison_data(db, user.id, comparison_id, limit, offset, changes_only)

    @app.delete("/api/documents/{document_id}", status_code=202)
    def delete_document(document_id: str, request: Request, user=Depends(authenticated), db=Depends(database)):
        throttle(request, "delete_document", 10)
        return request_deletion(db, user.id, document_id)

    @app.get("/api/deletions")
    def deletions(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
                  user=Depends(authenticated), db=Depends(database)):
        query = select(DocumentDeletion).where(DocumentDeletion.owner_id == user.id)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(DocumentDeletion.created_at.desc(), DocumentDeletion.document_id).limit(limit).offset(offset)).all()
        return {"items": [deletion_data(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    dist = ROOT / "apps" / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
