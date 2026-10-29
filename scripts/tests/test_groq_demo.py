"""Demo setup checks with fake tokenizer/model lookups; no network or inference."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import configure_groq_demo as demo
from app.config import Settings
from app.groq_config import groq_readiness


class GroqDemoSetupTests(unittest.TestCase):
    def setUp(self):
        directory = demo.ROOT / "var/tests"
        directory.mkdir(parents=True, exist_ok=True)
        self.root = Path(self.enterContext(TemporaryDirectory(dir=directory)))
        (self.root / "var").mkdir()
        self.path = self.root / "var/groq-demo-evidence.json"
        self.evidence = {"model": "openai/gpt-oss-20b", "account_reference": "test-account",
            "account_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "account_and_existing_usage_verified": True,
            "limits": {"requests_per_minute": 30, "requests_per_day": 1000,
                       "tokens_per_minute": 8000, "tokens_per_day": 200000}}
        self.path.write_text(json.dumps(self.evidence), encoding="utf-8")
        self.original = b"# Preserve user configuration\r\nGROQ_API_KEY=test-only-private\r\n"
        (self.root / ".env").write_bytes(self.original)
        self.settings = Settings(groq_api_key="test-only-private", groq_model="openai/gpt-oss-20b",
                                groq_readiness_file=str(self.root / "var/groq-readiness.json"))
        self.enterContext(patch.object(demo, "ROOT", self.root))
        self.enterContext(patch.object(demo, "EVIDENCE", self.path))
        self.enterContext(patch.object(demo.Settings, "from_env", return_value=self.settings))
        self.estimator = self.enterContext(patch.object(demo, "verify_estimator", return_value={"test_only": True}))
        self.model = self.enterContext(patch.object(demo, "verify_model"))

    def test_success_enables_only_after_checks_preserves_env_and_keeps_account_stable(self):
        with redirect_stdout(StringIO()):
            demo.configure()
        readiness = groq_readiness(replace(self.settings, groq_enabled=True))
        self.assertIsNotNone(readiness.policy)
        self.assertEqual([limit.capacity for limit in readiness.policy.limits], [30, 1000, 8000, 200000])
        self.assertEqual((self.root / ".env").read_bytes(), self.original + b"TERMSLENS_GROQ_ENABLED=true\n")
        record = Path(self.settings.groq_readiness_file).read_bytes()
        with redirect_stdout(StringIO()):
            demo.configure()
        self.assertEqual(Path(self.settings.groq_readiness_file).read_bytes(), record)
        self.estimator.assert_called_once()
        self.model.assert_called_once()

    def test_unconfirmed_account_never_reaches_network_or_enables(self):
        self.evidence["account_and_existing_usage_verified"] = False
        self.path.write_text(json.dumps(self.evidence), encoding="utf-8")
        with self.assertRaises(demo.SetupError):
            demo.configure()
        self.model.assert_not_called()
        self.assertEqual((self.root / ".env").read_bytes(), self.original)

    def test_model_failure_preserves_settings_and_does_not_create_readiness(self):
        self.model.side_effect = OSError("test-only-private")
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            demo.main()
        self.assertNotIn("test-only-private", output.getvalue())
        self.assertFalse(Path(self.settings.groq_readiness_file).exists())
        self.assertEqual((self.root / ".env").read_bytes(), self.original)

    def test_failed_token_verification_does_not_call_provider(self):
        self.estimator.side_effect = demo.SetupError("Token verification failed.")
        with self.assertRaises(demo.SetupError):
            demo.configure()
        self.model.assert_not_called()
        self.assertFalse(Path(self.settings.groq_readiness_file).exists())

    def test_existing_invalid_record_is_preserved_without_network(self):
        path = Path(self.settings.groq_readiness_file)
        path.write_bytes(b"existing-user-record")
        with self.assertRaises(demo.SetupError):
            demo.configure()
        self.assertEqual(path.read_bytes(), b"existing-user-record")
        self.model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
