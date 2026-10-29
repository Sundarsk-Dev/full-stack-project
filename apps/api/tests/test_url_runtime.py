"""Process limits, safe child protocol and test database isolation; never live HTTP."""
from copy import deepcopy
from io import BytesIO
import json
import os
import subprocess
import sys
from threading import Timer
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.config import ROOT
from app.url_guard import FetchError
from app.url_runtime import URLFetcher, main, perform
from tests.test_url_fetch import FakeFetch, URL, response

DATABASE = "mysql+pymysql://termslens_test:synthetic-secret@127.0.0.1/termslens_test"


class URLRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.result = FakeFetch().operation().run(URL)
        self.fetcher = URLFetcher(DATABASE, test=True)
        self.request = {"url": URL, "operation": "capture", "database_url": DATABASE, "test": True, "timeout_seconds": 1}

    def test_parent_uses_stdin_for_private_input_and_clears_inherited_keys(self):
        def run(command, **options):
            self.assertNotIn(DATABASE, " ".join(command))
            self.assertNotIn(URL, " ".join(command))
            self.assertNotIn("GROQ_API_KEY", options["env"])
            self.assertNotIn("TERMSLENS_DATABASE_URL", options["env"])
            self.assertEqual(json.loads(options["input"])["database_url"], DATABASE)
            self.assertEqual(json.loads(options["input"])["test"], True)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            return subprocess.CompletedProcess(command, 0, json.dumps({"status": "ok", "result": self.result}).encode())
        with patch.dict(os.environ, {"GROQ_API_KEY": "synthetic-provider-secret", "TERMSLENS_DATABASE_URL": "production-private"}), \
                patch("app.url_runtime.subprocess.run", side_effect=run) as process:
            result = self.fetcher.fetch(URL)
        self.assertEqual(result, self.result)
        process.assert_called_once()

    def test_parent_rejects_malformed_or_inconsistent_child_results(self):
        wrong_hash = deepcopy(self.result)
        wrong_hash["text_sha256"] = "0" * 64
        wrong_heading = deepcopy(self.result)
        wrong_heading["headings"][0]["end"] += 1
        wrong_origin = deepcopy(self.result)
        wrong_origin["source_url"] = "https://other.example.org/terms"
        excessive = deepcopy(self.result)
        excessive["requests"] = 9
        for reply in [b"private exception text", b'{}', json.dumps({"status": "failed", "reason": "private-exception"}).encode(),
                      *(json.dumps({"status": "ok", "result": result}).encode() for result in [wrong_hash, wrong_heading, wrong_origin, excessive])]:
            with patch("app.url_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, reply)):
                with self.assertRaises(FetchError) as error: self.fetcher.fetch(URL)
            self.assertEqual(str(error.exception), "invalid_child_result")

    def test_discovery_child_cannot_supply_cross_origin_or_too_many_candidates(self):
        result = FakeFetch({URL: response(b'<a href="/privacy">Privacy</a>')}).operation().run(URL, "discovery")
        def completed(value):
            return subprocess.CompletedProcess([], 0, json.dumps({"status": "ok", "result": value}).encode())
        with patch("app.url_runtime.subprocess.run", return_value=completed(result)):
            self.assertEqual(self.fetcher.fetch(URL, operation="discovery"), result)
        for value in [[{**result["candidates"][0], "url": "https://external.example.org/privacy"}], result["candidates"] * 5]:
            with patch("app.url_runtime.subprocess.run", return_value=completed({**result, "candidates": value})):
                with self.assertRaises(FetchError): self.fetcher.fetch(URL, operation="discovery")

    def test_safe_child_failure_and_invalid_input_never_leak_private_values(self):
        reply = json.dumps({"status": "failed", "reason": "robots_denied"}).encode()
        with patch("app.url_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, reply)):
            with self.assertRaisesRegex(FetchError, "robots_denied"): self.fetcher.fetch(URL)
        for timeout in [0, -1, 61, True, "1", float("inf"), float("nan")]:
            with patch("app.url_runtime.subprocess.run") as process, self.assertRaises(FetchError):
                self.fetcher.fetch(URL, timeout_seconds=timeout)
            process.assert_not_called()
        self.assertEqual(str(FetchError("private exception body")), "fetch_failed")

    def test_child_validates_request_and_cancels_its_watchdog(self):
        output, timers = BytesIO(), []
        def timer(*args, **kwargs):
            result = Timer(*args, **kwargs)
            timers.append(result)
            return result
        with patch("app.url_runtime.sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(self.request).encode()))), \
                patch("app.url_runtime.sys.stdout", SimpleNamespace(buffer=output)), patch("app.url_runtime.perform", return_value=self.result), \
                patch("app.url_runtime.Timer", side_effect=timer), patch("app.url_runtime.os._exit") as terminate:
            main()
            self.assertEqual(len(timers), 1)
            self.assertFalse(timers[0].is_alive())
            terminate.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["status"], "ok")
        self.assertNotIn(b"synthetic-secret", output.getvalue())
        for change in [{"test": "true"}, {"timeout_seconds": 61}, {"timeout_seconds": True}, {"url": "http://127.0.0.1/"}, {"unexpected": "private"}]:
            output = BytesIO()
            with patch("app.url_runtime.sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps({**self.request, **change}).encode()))), \
                    patch("app.url_runtime.sys.stdout", SimpleNamespace(buffer=output)), patch("app.url_runtime.perform") as operation:
                main()
            operation.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    def test_child_propagates_test_database_selection_and_closes_engine(self):
        engine, factory, operation = Mock(), Mock(), Mock()
        operation.run.return_value = self.result
        with patch("app.url_runtime.make_database", return_value=(engine, factory)) as database, \
                patch("app.url_runtime.FetchOperation", return_value=operation):
            self.assertEqual(perform(self.request), self.result)
        database.assert_called_once_with(DATABASE, test=True)
        engine.dispose.assert_called_once()
        wrong = {**self.request, "database_url": DATABASE.replace("/termslens_test", "/termslens_db")}
        with self.assertRaisesRegex(FetchError, "storage_unavailable"): perform(wrong)

    def test_timeout_interrupt_and_communication_failure_kill_and_reap_child(self):
        real_popen = subprocess.Popen
        for failure in ["timeout", "interrupt", "communication"]:
            children = []
            def popen(_command, **options):
                child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **options)
                children.append(child)
                return child
            try:
                with patch("subprocess.Popen", side_effect=popen):
                    if failure != "timeout":
                        error = KeyboardInterrupt if failure == "interrupt" else OSError("private fixture detail")
                        with patch.object(real_popen, "communicate", side_effect=error):
                            with self.assertRaises(KeyboardInterrupt if failure == "interrupt" else FetchError):
                                self.fetcher.fetch(URL, timeout_seconds=.2)
                    else:
                        with self.assertRaisesRegex(FetchError, "fetch_deadline"): self.fetcher.fetch(URL, timeout_seconds=.2)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
            finally:
                for child in children:
                    if child.poll() is None: child.kill()
                    child.wait(timeout=3)

    def test_child_watchdog_bounds_stalled_dns_or_database_without_parent(self):
        script = ("import time; from app import url_runtime as runtime; print(time.monotonic(), flush=True); "
                  "runtime.perform = lambda request: time.sleep(30); runtime.main()")
        request = {**self.request, "timeout_seconds": .2}
        result = subprocess.run([sys.executable, "-c", script], input=json.dumps(request).encode(), capture_output=True,
            cwd=ROOT / "apps/api", timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 1)
        self.assertLess(monotonic() - float(result.stdout), 3)
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__": unittest.main()
