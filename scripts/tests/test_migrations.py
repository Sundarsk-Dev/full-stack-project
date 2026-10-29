"""Offline DDL from the workspace path; never load credentials or connect."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OfflineMigrationTests(unittest.TestCase):
    def test_url_migration_from_root_uses_exact_paths_without_credentials_or_engine(self):
        script = """
from unittest.mock import patch
import sys
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
with patch('dotenv.load_dotenv', side_effect=AssertionError('offline must not read credentials')), \\
     patch('sqlalchemy.create_engine', side_effect=AssertionError('offline must not connect')):
    command.upgrade(Config(sys.argv[1]), '20261028_review:head', sql=True)
    from app.db import SCHEMA_REVISION
    assert SCHEMA_REVISION == ScriptDirectory.from_config(Config(sys.argv[1])).get_current_head()
"""
        result = subprocess.run([sys.executable, "-c", script, str(ROOT / "apps/api/alembic.ini")],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CREATE TABLE fetch_hosts", result.stdout)
        self.assertIn("20261029_url", result.stdout)
        self.assertIn("lease_expires_at DATETIME(6)", result.stdout)
        self.assertNotIn("DROP TABLE", result.stdout)
        self.assertIn("ALTER TABLE jobs ADD COLUMN input_url VARCHAR(2048)", result.stdout)
        self.assertIn("ALTER TABLE document_versions ADD COLUMN capture_metadata JSON", result.stdout)
        self.assertIn("20261029_capture", result.stdout)
        self.assertIn("20261029_versions", result.stdout)
        self.assertIn("CREATE TABLE comparisons", result.stdout)
        self.assertIn("CREATE TABLE document_deletions", result.stdout)
        self.assertIn("20261029_lifecycle", result.stdout)
        self.assertIn("ROW_NUMBER() OVER (PARTITION BY version_id", result.stdout)
        self.assertIn("CONSTRAINT uq_analysis_revision UNIQUE", result.stdout)
        self.assertLess(result.stdout.index("CREATE INDEX ix_jobs_result_id"), result.stdout.index("DROP INDEX result_id"))


if __name__ == "__main__": unittest.main()
