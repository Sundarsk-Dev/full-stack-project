from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from scripts import collect_eval_sources as sources


class SourceCollectionTests(unittest.TestCase):
    def setUp(self):
        root = sources.ROOT / "var/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name)
        self.catalog = json.loads(sources.CATALOG.read_text(encoding="utf-8"))
        self.item = sources.documents(self.catalog)[0]

    def fetch(self, body=b"<p>Policy text.</p>", *, status=200, headers=None, stream=None):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8", **(headers or {})},
                                  stream=stream or httpx.ByteStream(body))
        return sources.fetch_one(self.item, self.destination, transport=httpx.MockTransport(handler))

    def test_catalog_proposes_eight_disjoint_families_and_sufficient_holdout(self):
        families = self.catalog["policy_families"]
        heldout = [family for family in families if family["split"] == "heldout"]
        self.assertEqual(len({family["id"] for family in families}), 8)
        self.assertEqual(sum(family["target_clauses"] for family in families), 112)
        self.assertEqual(len(heldout), 3)
        self.assertEqual(sum(family["target_clauses"] for family in heldout), 42)
        self.assertEqual(len(sources.documents(self.catalog)), 11)
        self.assertIn("Unverified", self.catalog["status"])

    def test_catalog_rejects_paths_unknown_hosts_credentials_and_duplicate_ids(self):
        for field, value in [("id", "../outside"), ("url", "https://127.0.0.1/private"),
                ("url", "http://wordpress.com/tos/"), ("url", "https://key@wordpress.com/tos/"),
                ("url", "https://wordpress.com:8000/tos/"), ("id", "groq-models")]:
            catalog = deepcopy(self.catalog)
            catalog["policy_families"][0]["documents"][0][field] = value
            with self.subTest(value=value), self.assertRaises(ValueError): sources.documents(catalog)

    def test_capture_preserves_unicode_hashes_provenance_and_pending_reviews_without_credentials(self):
        body = '<h1>Policy \U0001f4dc</h1><script>secret script</script><p>No sale &amp; no tracking.</p><footer>License evidence.</footer>'.encode()
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-secret", "HTTPS_PROXY": "http://proxy.invalid"}), \
                patch.object(httpx, "Client", wraps=httpx.Client) as client:
            capture = self.fetch(body)
        metadata = json.loads((capture / "capture.json").read_text(encoding="utf-8"))
        text = (capture / "text.txt").read_text(encoding="utf-8")
        self.assertEqual(metadata["raw_sha256"], sha256(body).hexdigest())
        self.assertEqual(metadata["text_sha256"], sha256(text.encode()).hexdigest())
        self.assertEqual(metadata["url"], self.item["url"])
        self.assertEqual((metadata["rights_review"], metadata["content_review"]), ("pending", "pending"))
        self.assertIn("\U0001f4dc", text)
        self.assertIn("No sale & no tracking.", text)
        self.assertIn("License evidence.", text)
        self.assertNotIn("secret script", text)
        self.assertNotIn("test-secret", json.dumps(metadata))
        self.assertFalse(client.call_args.kwargs["trust_env"])
        self.assertFalse(client.call_args.kwargs["follow_redirects"])
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("authorization", self.requests[0].headers)

    def test_redirects_and_errors_stop_without_writing_or_retrying(self):
        for status in [301, 302, 401, 403, 429, 500]:
            with self.subTest(status=status), self.assertRaises(sources.CollectionError):
                self.fetch(status=status, headers={"location": "http://127.0.0.1/private"})
            self.assertEqual(len(self.requests), 1)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_declared_and_streamed_size_limits_stop_consumption(self):
        class LargeStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"x" * sources.MAX_BYTES
                yield b"x"
                raise AssertionError("Do not keep reading")
        for options in [{"headers": {"content-length": str(sources.MAX_BYTES + 1)}}, {"stream": LargeStream()}]:
            with self.assertRaisesRegex(sources.CollectionError, "source_too_large"): self.fetch(**options)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_unsupported_types_encoding_and_empty_text_are_rejected(self):
        for options in [{"headers": {"content-type": "application/pdf"}}, {"headers": {"content-encoding": "gzip"}},
                        {"body": b"<script>not policy text</script>"}]:
            with self.assertRaises(sources.CollectionError): self.fetch(**options)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_network_errors_do_not_echo_private_exception_text(self):
        def handler(request): raise httpx.ConnectError("private exception")
        with self.assertRaises(sources.CollectionError) as error:
            sources.fetch_one(self.item, self.destination, transport=httpx.MockTransport(handler))
        self.assertEqual(str(error.exception), "fetch_failed")

    def test_new_capture_never_overwrites_previous_text(self):
        first = self.fetch(b"<p>Original policy.</p>")
        second = self.fetch(b"<p>Changed policy.</p>")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "text.txt").read_text(), "Original policy.")
        self.assertEqual((second / "text.txt").read_text(), "Changed policy.")

    def test_default_cli_lists_candidates_without_network(self):
        output = StringIO()
        with patch("sys.argv", ["collect_eval_sources.py"]), redirect_stdout(output), \
                patch.object(sources, "collect_in_child") as child, patch.object(sources, "fetch_one") as fetch:
            sources.main()
        child.assert_not_called()
        fetch.assert_not_called()
        self.assertIn("UNVERIFIED", output.getvalue())

    def test_parent_timeout_is_reported_without_retry(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("child", 15)) as run:
            status = sources.collect_in_child(self.item["id"])
        self.assertEqual(status["reason"], "total_timeout")
        run.assert_called_once()

    def test_failed_or_malformed_child_cannot_report_a_successful_capture(self):
        for result in [subprocess.CompletedProcess([], 0, b"invalid"),
                subprocess.CompletedProcess([], 1, json.dumps({"id": self.item["id"], "status": "captured"}).encode())]:
            with patch.object(subprocess, "run", return_value=result):
                self.assertEqual(sources.collect_in_child(self.item["id"])["reason"], "child_failed")


if __name__ == "__main__": unittest.main()
