"""A single durable MySQL worker. Run separately from the API."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import logging
from threading import Event, Thread
import time

from sqlalchemy import and_, func, or_, select, text as sql_text, update
from sqlalchemy.exc import SQLAlchemyError

from .analysis import analyze
from .ai_review import review_job
from .config import ROOT, Settings
from .db import make_database, utcnow
from .diagnostics import ERRORS, configure_logging, emit, input_error_code
from .groq_config import groq_readiness
from .groq_transport import GroqTransport
from .models import AICall, Analysis, CategoryScore, Clause, ClauseFinding, Document, DocumentVersion, Job, User, new_id
from .publication import finding_key, review_metadata
from .snapshots import SnapshotStore
from .url_guard import FetchError
from .url_runtime import URLFetcher
from .scoring import PROFILE_REVISION
from .versions import latest_version, reusable, same_capture
from .deletion import process_deletion

LOG = logging.getLogger("termslens.worker")


class LeaseLost(Exception):
    pass


def claim(factory, lease_seconds=120):
    with factory.begin() as db:
        now = utcnow()
        if db.scalar(select(Job.id).where(Job.state == "running", Job.lease_expires_at > now).limit(1)):
            return None
        job = db.scalar(select(Job).where(or_(Job.state == "queued", and_(Job.state == "running", Job.lease_expires_at <= now)))
                        .order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        if job.attempts >= 3:
            job.error_code, job.error_stage = "attempts_exhausted", job.error_stage or job.stage
            job.state, job.stage, job.error = "failed", "failed", ERRORS["attempts_exhausted"]
            job.finished_at, job.lease_token, job.lease_expires_at = now, None, None
            return None
        job.state, job.stage, job.error = "running", "capturing", None
        job.attempts += 1
        job.lease_token, job.lease_expires_at = new_id(), now + timedelta(seconds=lease_seconds)
        emit("job_claimed", job_id=job.id, request_id=job.request_id, stage=job.stage, state=job.state)
        return job.id, job.lease_token


def leased_job(db, job_id, token):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.state != "running" or job.lease_token != token or job.lease_expires_at <= utcnow():
        raise LeaseLost()
    return job


def capture(factory, job_id, token, *, fetcher=None, snapshots=None):
    # Read the durable input, then release the transaction for network/filesystem work.
    with factory.begin() as db:
        job = leased_job(db, job_id, token)
        identity = job.owner_id, job.id, job.request_hash
        url = job.input_url if not job.version_id else None
        if job.document_id:
            document = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
            if not document or document.owner_id != job.owner_id or document.tombstoned:
                raise ValueError("The source document is unavailable.")
    fetched = None
    if url:
        if snapshots is None:
            raise ValueError("The source document is unavailable.")
        fetched = snapshots.load(*identity, url)
        if fetched is None:
            if fetcher is None: raise FetchError("storage_unavailable")
            fetched = snapshots.save(*identity, url, fetcher.fetch(url))
    with factory.begin() as db:
        job = leased_job(db, job_id, token)
        if identity != (job.owner_id, job.id, job.request_hash):
            raise ValueError("The source document is unavailable.")
        if job.version_id:
            version = db.get(DocumentVersion, job.version_id)
            document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update()) if version else None
            if (not document or document.owner_id != job.owner_id or document.tombstoned
                    or job.document_id not in {None, document.id} or document.document_type != job.document_type):
                raise ValueError("The source document is unavailable.")
        else:
            if job.input_url != url:
                raise ValueError("The source document is unavailable.")
            if job.operation == "refresh":
                document = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
                if (not document or document.owner_id != job.owner_id or document.tombstoned
                        or document.document_type != job.document_type
                        or document.source_kind != ("url" if url else "paste")):
                    raise ValueError("The source document is unavailable.")
            elif job.operation == "new" and job.document_id is None:
                document = Document(owner_id=job.owner_id, title=job.title, document_type=job.document_type,
                                    source_kind="url" if url else "paste")
                db.add(document)
                db.flush()
            else:
                raise ValueError("The source document is unavailable.")
            previous = latest_version(db, document.id)
            source_text = fetched["text"] if fetched else job.input_text
            extractor = fetched["extractor"] if fetched else "paste-v1"
            headings = fetched["headings"] if fetched else []
            job.capture_changed = not same_capture(previous, source_text, extractor, headings)
            job.checked_at = snapshots.validate(fetched, url) if fetched else utcnow()
            job.check_metadata = {k: fetched[k] for k in ("source_url", "final_url", "raw_sha256", "text_sha256")} if fetched else None
            if not job.capture_changed:
                version = previous
            else:
                version = DocumentVersion(document_id=document.id, text=source_text,
                    content_hash=sha256(source_text.encode("utf-8")).hexdigest(), extractor_version=extractor,
                    version_number=previous.version_number + 1 if previous else 1,
                    parent_id=previous.id if previous else None, captured_at=job.checked_at)
                db.add(version)
            if fetched and job.capture_changed:
                version.text, version.content_hash = fetched["text"], fetched["text_sha256"]
                version.extractor_version = fetched["extractor"]
                version.captured_at = snapshots.validate(fetched, url)
                version.snapshot_path = snapshots.relative_path(*identity)
                version.capture_metadata = {k: fetched[k] for k in ("source_url", "final_url", "retrieved_at", "requests",
                    "redirects", "elapsed_seconds", "content_type", "raw_representation", "raw_sha256", "text_sha256", "headings")}
            db.flush()
            job.version_id = version.id
        job.document_id = document.id
        job.stage = "segmenting"
        return version.text, document.document_type


def stage(factory, job_id, token, value):
    with factory.begin() as db:
        leased_job(db, job_id, token).stage = value
    emit("job_stage", job_id=job_id, stage=value)


def publish(factory, job_id, token, result, *, review=None):
    with factory.begin() as db:
        # Analysis INSERT checks the owner FK; acquire it before job/document locks
        # so enqueue/deletion cannot hold the owner while waiting on this document.
        owner_id = db.scalar(select(Job.owner_id).where(Job.id == job_id))
        if owner_id is None: raise LeaseLost()
        db.execute(select(User.id).where(User.id == owner_id).with_for_update())
        job = leased_job(db, job_id, token)
        version = db.get(DocumentVersion, job.version_id)
        document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update())
        if document is None or document.owner_id != job.owner_id or document.tombstoned:
            raise ValueError("The source document is unavailable.")
        if result["text"] != version.text:
            raise ValueError("The analysis did not preserve its source text.")
        score = result["score"]
        metadata, ai_calls, terminal_state = review_metadata(db, job, version, result, review)
        analysis = Analysis(owner_id=job.owner_id, version_id=version.id, requested_mode=job.requested_mode,
                            revision_number=(db.scalar(select(func.max(Analysis.revision_number)).where(Analysis.version_id == version.id)) or 0) + 1,
                            profile_revision=PROFILE_REVISION,
                            engine=result["engine"], supported_groups=result["supported_groups"], **score, **metadata)
        db.add(analysis)
        db.flush()
        for item in result["clauses"]:
            if not (0 <= item["start"] < item["end"] <= len(version.text)) or version.text[item["start"]:item["end"]] != item["text"]:
                raise ValueError("Evidence could not be matched to the source.")
            clause = Clause(analysis_id=analysis.id, local_id=item["id"], start=item["start"], end=item["end"],
                            substantive=item["substantive"], unrated_reason=item["unrated_reason"])
            db.add(clause)
            db.flush()
            for finding in item["findings"]:
                if not (item["start"] <= finding["start"] < finding["end"] <= item["end"]) or version.text[finding["start"]:finding["end"]] != finding["quote"]:
                    raise ValueError("Evidence could not be matched to the source.")
                db.add(ClauseFinding(clause_id=clause.id, ai_call_id=ai_calls.get(finding_key(item["id"], finding)), **finding))
        for item in result["categories"]:
            db.add(CategoryScore(analysis_id=analysis.id, category=item["id"], max_severity=item["max_severity"],
                                 unknown=item["unknown"], applicable=item["applicable"], accepted_count=item["accepted_count"]))
        job.result_id, job.state, job.stage = analysis.id, terminal_state, "complete"
        job.finished_at, job.lease_token, job.lease_expires_at = utcnow(), None, None
        result_id = analysis.id
    emit("job_published", job_id=job_id, stage="complete", state=terminal_state)
    return result_id


def reuse_report(factory, job_id, token, baseline, model):
    with factory.begin() as db:
        job = leased_job(db, job_id, token)
        if job.operation != "refresh" or job.capture_changed is not False:
            return None
        version = db.get(DocumentVersion, job.version_id)
        document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update())
        if (not document or document.owner_id != job.owner_id or document.tombstoned
                or document.id != job.document_id or document.document_type != job.document_type):
            raise ValueError("The source document is unavailable.")
        # A recovering job with provider attempts must finish its own saved work.
        if db.scalar(select(AICall.id).where(AICall.job_id == job.id).limit(1)):
            return None
        analysis = db.scalar(select(Analysis).where(Analysis.version_id == version.id, Analysis.owner_id == job.owner_id)
                             .order_by(Analysis.revision_number.desc()).limit(1))
        if not reusable(analysis, job, baseline, model): return None
        job.result_id, job.result_reused = analysis.id, True
        job.state = "partial" if analysis.review_status == "limited" else "done"
        job.stage, job.finished_at, job.lease_token, job.lease_expires_at = "complete", utcnow(), None, None
        result_id = analysis.id
    emit("report_reused", job_id=job_id, stage="complete")
    return result_id


def fail(factory, job_id, token, error_code, permanent=False):
    with factory.begin() as db:
        try:
            job = leased_job(db, job_id, token)
        except LeaseLost:
            return
        terminal = permanent or job.attempts >= 3
        job.error_code, job.error_stage = error_code, job.stage
        job.state, job.stage = ("failed", "failed") if terminal else ("queued", "queued")
        job.error = ERRORS[error_code]
        job.finished_at = utcnow() if terminal else None
        job.lease_token, job.lease_expires_at = None, None


@contextmanager
def heartbeat(factory, job_id, token, lease_seconds):
    stop = Event()

    def renew():
        while not stop.wait(max(1, lease_seconds / 3)):
            try:
                with factory.begin() as db:
                    now = utcnow()
                    changed = db.execute(update(Job).where(Job.id == job_id, Job.state == "running", Job.lease_token == token,
                                                         Job.lease_expires_at > now)
                                         .values(lease_expires_at=now + timedelta(seconds=lease_seconds))).rowcount
                    if not changed:
                        return
            except SQLAlchemyError:
                # Publication also checks token and expiry; failed renewal never extends a lease.
                return

    thread = Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def process_claim(factory, claimed, *, policy=None, transport=None, settings=None, fetcher=None, snapshots=None):
    job_id, token = claimed
    try:
        if settings is not None:
            fetcher = fetcher or URLFetcher(settings.database_url, test=settings.test_mode)
            snapshots = snapshots or SnapshotStore(ROOT / "var" / ("tests/snapshots" if settings.test_mode else "snapshots"))
        text, document_type = capture(factory, job_id, token, fetcher=fetcher, snapshots=snapshots)
        stage(factory, job_id, token, "analyzing")
        with factory() as db:
            job = db.get(Job, job_id)
            mode, operation = job.requested_mode, job.operation
            version = db.get(DocumentVersion, job.version_id)
            extractor, headings = version.extractor_version, (version.capture_metadata or {}).get("headings", [])
        baseline = analyze(text, document_type, extractor=extractor, headings=headings) if mode == "rules_only" or operation == "refresh" else None
        if operation == "refresh":
            model = settings.groq_model if settings else policy.model if policy else None
            reused = reuse_report(factory, job_id, token, baseline, model)
            if reused: return reused
        if mode == "hybrid" and settings is not None:
            readiness = groq_readiness(settings)
            policy = readiness.policy
            transport = GroqTransport(settings.groq_api_key, settings.groq_model) if policy else None
        review = review_job(factory, job_id, token, policy=policy, transport=transport) if mode == "hybrid" else None
        result = review.result if review else baseline
        stage(factory, job_id, token, "publishing")
        return publish(factory, job_id, token, result, review=review)
    except LeaseLost:
        emit("lease_lost", job_id=job_id)
    except FetchError as error:
        code = str(error)
        emit("job_rejected", job_id=job_id, error_code=code)
        fail(factory, job_id, token, code, permanent=True)
    except ValueError as error:
        code = input_error_code(error)
        emit("job_rejected", job_id=job_id, error_code=code, error_type=type(error).__name__)
        fail(factory, job_id, token, code, permanent=True)
    except Exception as error:
        # SQL/driver exceptions can contain input text or connection credentials.
        emit("job_interrupted", job_id=job_id, error_code="worker_error", error_type=type(error).__name__)
        try:
            fail(factory, job_id, token, "worker_error")
        except SQLAlchemyError:
            LOG.error("Could not persist failure for job %s; lease recovery will retry", job_id)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued or expired job and exit")
    parser.add_argument("--test", action="store_true", help="Use termslens_test with external review disabled")
    args = parser.parse_args()
    settings = Settings.from_env(test=args.test)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    configure_logging()
    engine = None
    try:
        engine, factory = make_database(settings.database_url, test=args.test)
        snapshots = SnapshotStore(ROOT / "var" / ("tests/snapshots" if settings.test_mode else "snapshots"))
        lock_parameters = {"name": "termslens_gate1_worker_test" if args.test else "termslens_gate1_worker"}
        # A dedicated connection owns the advisory lock for this worker's lifetime.
        with engine.connect() as lock:
            if lock.execute(sql_text("SELECT GET_LOCK(:name, 0)"), lock_parameters).scalar() != 1:
                raise ValueError("Another TermsLens worker is already active.")
            lock.commit()
            try:
                if args.once:
                    LOG.info("Worker started. Checking for one job (--once).")
                else:
                    LOG.info("Worker started. Waiting for jobs; keep this terminal open. Ctrl+C stops it.")
                while True:
                    # Detect loss of the lock connection before claiming more work.
                    if lock.execute(sql_text("SELECT IS_USED_LOCK(:name) = CONNECTION_ID()"), lock_parameters).scalar() != 1:
                        raise ValueError("Worker lock was lost; restart the worker.")
                    lock.commit()
                    process_deletion(factory, snapshots)
                    claimed = claim(factory, settings.lease_seconds)
                    if claimed:
                        with heartbeat(factory, *claimed, settings.lease_seconds):
                            process_claim(factory, claimed, settings=settings, snapshots=snapshots)
                    if args.once:
                        break
                    time.sleep(1)
            finally:
                lock.execute(sql_text("SELECT RELEASE_LOCK(:name)"), lock_parameters)
                lock.commit()
    except KeyboardInterrupt:
        LOG.info("Worker stopped.")
    except (SQLAlchemyError, ValueError, ImportError):
        LOG.error("Worker unavailable. Check MySQL, dependencies, migrations and that only one worker runs.")
        raise SystemExit(1)
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
