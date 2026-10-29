"""Acceptance-only child: real test worker/database, synthetic URL fetch and no Groq."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, patch


def run_worker(database_url, owner):
    environment = dict(os.environ, TERMSLENS_RUN_MYSQL_TESTS="1", TERMSLENS_ACCEPTANCE_OWNER=owner,
        TERMSLENS_TEST_DATABASE_URL=database_url, TERMSLENS_DATABASE_URL="must-not-use-production",
        TERMSLENS_GROQ_ENABLED="true", GROQ_API_KEY="fixture-key-never-send", TERMSLENS_GROQ_MODEL="fixture-model",
        TERMSLENS_GROQ_READINESS_FILE="must-not-read-readiness")
    # URLs/keys are never argv or printed. Windows helpers stay hidden.
    return subprocess.run([sys.executable, "-m", "tests.mysql_worker_child"],
        cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True,
        timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    if os.getenv("TERMSLENS_RUN_MYSQL_TESTS") != "1": raise ValueError("Acceptance opt-in required.")
    from sqlalchemy import select
    from app import worker
    from app.config import ROOT, Settings
    from app.db import make_database
    from app.groq_config import groq_readiness
    from app.models import DocumentDeletion, Job, User
    from app.snapshots import SnapshotStore
    from tests.mysql_fixture import TEXT
    from tests.test_url_jobs import captured
    settings = Settings.from_env(test=True)
    owner = os.environ["TERMSLENS_ACCEPTANCE_OWNER"]
    def validate(config):
        assert config.test_mode and config.database_url == settings.database_url
        assert not (config.groq_enabled or config.groq_api_key or config.groq_model or config.groq_readiness_file)
    validate(settings)
    engine, factory = make_database(settings.database_url, test=True)
    try:
        with factory() as db:
            user = db.get(User, owner)
            assert user and user.username.startswith("lifecycle_")
            assert db.scalar(select(Job.id).where(Job.owner_id != owner, Job.state.in_(["queued", "running"])).limit(1)) is None
            assert db.scalar(select(DocumentDeletion.document_id).where(DocumentDeletion.owner_id != owner, DocumentDeletion.state == "pending").limit(1)) is None
    finally: engine.dispose()
    def database(url, *, test):
        assert test and url == settings.database_url
        return make_database(url, test=True)
    def snapshots(root):
        assert Path(root).resolve() == (ROOT / "var/tests/snapshots").resolve()
        return SnapshotStore(root)
    def fetcher(url, *, test):
        assert test and url == settings.database_url
        return Mock(fetch=Mock(return_value=captured(TEXT + "\nYour information supports our commercial partnerships.")))
    def readiness(config):
        validate(config)
        return groq_readiness(config)
    with patch.object(sys, "argv", ["app.worker", "--test", "--once"]), \
         patch.object(worker, "make_database", side_effect=database), \
         patch.object(worker, "SnapshotStore", side_effect=snapshots) as stores, \
         patch.object(worker, "URLFetcher", side_effect=fetcher) as fetches, \
         patch.object(worker, "groq_readiness", side_effect=readiness), \
         patch.object(worker, "GroqTransport", side_effect=AssertionError("No provider allowed")) as provider:
        worker.main()
        stores.assert_called_once()
        fetches.assert_called_once()
        provider.assert_not_called()


if __name__ == "__main__":
    try: main()
    except BaseException:
        print("Isolated worker acceptance failed; inspect test setup privately.", file=sys.stderr)
        raise SystemExit(1) from None
