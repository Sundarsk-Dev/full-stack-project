"""No-network URL contracts. Real MySQL locks and public fetches remain acceptance work."""
from contextlib import contextmanager
from datetime import timedelta
import gzip
from hashlib import sha256
from io import BytesIO
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from app.db import utcnow
from app.models import FetchHost
from app.url_content import candidates, extract
from app.url_fetch import FetchOperation
from app.url_guard import Address, FetchError, HTTPResult, MAX_BYTES, PinnedConnection, public_ip, read_body, request_pinned, resolve_public, validate_url
from app.url_robots import parse_robots
from app.url_throttle import HostThrottle
from tests.test_worker import WorkerFixture

IP = "93.184.216.34"
URL = "https://policy.example.org/terms"
HTML = b'<html lang="en"><head><title>Policy</title></head><body><h1>Terms</h1><p>We retain your information for thirty days.</p></body></html>'


def answer(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return family, socket.SOCK_STREAM, 6, "", (ip, 443)


def response(body=HTML, status=200, **headers):
    return HTTPResult(status, {"Content-Type": "text/html; charset=utf-8", **headers}, body)


class FakeFetch:
    def __init__(self, pages=None):
        self.pages, self.calls, self.slots, self.now = pages or {}, [], [], 0

    @contextmanager
    def slot(self, host, interval, remaining):
        self.slots.append((host, interval, remaining))
        yield 0

    def send(self, target, address, timeout):
        self.calls.append((target, address, timeout))
        if target.url in self.pages: return self.pages[target.url]
        if target.path == "/robots.txt": return response(b"", 404)
        return response()

    def resolve(self, target):
        return resolve_public(target, lambda *_args, **_kwargs: [answer(IP)])

    def wait(self, seconds): self.now += seconds

    def operation(self, **options):
        return FetchOperation(self.slot, resolver=self.resolve, sender=self.send, clock=lambda: self.now, wait=self.wait, **options)


class URLGuardTests(unittest.TestCase):
    def test_private_special_obfuscated_and_credential_urls_are_rejected(self):
        invalid = ["file:///secret", "ftp://policy.example.org/", "https://user:pass@policy.example.org/", "https://localhost/",
            "https://127.0.0.1/", "https://10.2.3.4/", "http://169.254.169.254/", "https://100.64.0.1/", "https://224.0.0.1/",
            "https://203.0.113.1/", "http://[::1]/", "https://[fc00::1]/", "https://[fe80::1]/", "https://[ff02::1]/",
            "http://[::ffff:127.0.0.1]/", "http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/", "http://2130706433/",
            "http://0x7f000001/", "http://127.000.0.1/", "https://policy.example.org:3306/", "https://%70olicy.example.org/",
            "https://policy.example.org\\@localhost/", "https://policy.example.org/\r\nHeader:value", "https://policy.example.org/%xx"]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(FetchError): validate_url(url)

    def test_normalization_preserves_query_reserved_escapes_idna_and_tls_host(self):
        target = validate_url("https://BÜCHER.example.org:443/policy/é?next=%2f#fragment")
        self.assertEqual(target.host, "xn--bcher-kva.example.org")
        self.assertEqual(target.url, "https://xn--bcher-kva.example.org/policy/%C3%A9?next=%2f")
        self.assertEqual(target.origin, ("https", target.host, 443))
        self.assertEqual(validate_url("http://[2606:4700:4700::1111]/").authority, "[2606:4700:4700::1111]")

    def test_every_dns_answer_must_be_public_before_connecting(self):
        for addresses in [[IP, "127.0.0.1"], [IP, "::1"], [], [IP] * 65]:
            with self.subTest(addresses=addresses), self.assertRaises(FetchError):
                resolve_public(validate_url(URL), lambda *_args, **_kwargs: [answer(ip) for ip in addresses])
        self.assertEqual(resolve_public(validate_url(URL), lambda *_args, **_kwargs: [answer(IP), answer(IP)]), [Address(socket.AF_INET, IP)])

    def test_socket_is_numeric_and_tls_and_host_use_the_original_hostname(self):
        sock, tls = Mock(), Mock()
        sock.getpeername.return_value = (IP, 443)
        context = Mock()
        context.wrap_socket.return_value = tls
        with patch("app.url_guard.socket.socket", return_value=sock), patch("app.url_guard.socket.getaddrinfo", side_effect=AssertionError("no second DNS")), \
                patch("app.url_guard.ssl.create_default_context", return_value=context):
            connection = PinnedConnection(validate_url(URL), Address(socket.AF_INET, IP), 3)
            connection.connect()
            sock.connect.assert_called_once_with((IP, 443))
            context.wrap_socket.assert_called_once_with(sock, server_hostname="policy.example.org")
            self.assertIs(connection.sock, tls)
            connection.close()
        mock_connection = Mock()
        mock_connection.getresponse.return_value = Body(HTML, {"Content-Type": "text/html"})
        with patch("app.url_guard.PinnedConnection", return_value=mock_connection):
            request_pinned(validate_url(URL), Address(socket.AF_INET, IP), 5)
        args, kwargs = mock_connection.request.call_args
        self.assertEqual(args, ("GET", "/terms"))
        self.assertEqual(kwargs["headers"]["Host"], "policy.example.org")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertNotIn("Cookie", kwargs["headers"])
        mock_connection.close.assert_called_once()

    def test_peer_mismatch_and_tls_failure_close_socket(self):
        for peer, failure in [("1.1.1.1", None), (IP, ssl.SSLError("private fixture detail"))]:
            sock = Mock()
            sock.getpeername.return_value = (peer, 443)
            context = Mock()
            context.wrap_socket.side_effect = failure
            with patch("app.url_guard.socket.socket", return_value=sock), patch("app.url_guard.ssl.create_default_context", return_value=context):
                with self.assertRaises((FetchError, ssl.SSLError)):
                    PinnedConnection(validate_url(URL), Address(socket.AF_INET, IP), 3).connect()
            sock.close.assert_called_once()

    def test_body_limits_apply_to_compressed_and_decompressed_data(self):
        raw = "Unicode \U0001f4dc source.".encode()
        self.assertEqual(read_body(Body(gzip.compress(raw), {"Content-Encoding": "gzip"})), raw)
        cases = [(b"x", {"Content-Length": str(MAX_BYTES + 1)}), (b"x" * (MAX_BYTES + 1), {}),
                 (gzip.compress(b"x" * (MAX_BYTES + 1)), {"Content-Encoding": "gzip"}),
                 (gzip.compress(raw)[:-4], {"Content-Encoding": "gzip"}),
                 (gzip.compress(raw) + gzip.compress(raw), {"Content-Encoding": "gzip"}),
                 (b"abc", {"Content-Length": "4"}), (b"abc", {"Content-Encoding": "br"})]
        for body, headers in cases:
            with self.subTest(headers=headers), self.assertRaises(FetchError): read_body(Body(body, headers))


class Body(BytesIO):
    status = 200

    def __init__(self, value, headers):
        super().__init__(value)
        self.headers = headers

    def getheader(self, key, default=None): return self.headers.get(key, default)


class RobotsAndContentTests(unittest.TestCase):
    def test_robots_groups_longest_match_allow_ties_and_wildcards(self):
        policy = parse_robots("User-agent: *\nDisallow: /\nUser-agent: TermsLens\nDisallow: /private*\nAllow: /private/public\n"
                              "Disallow: /*?token=*\nDisallow: /exact$\nAllow: /exact$\nCrawl-delay: 2\nRequest-rate: 1/5\n"
                              "User-agent: termslens\nDisallow: /other\n")
        for path, allowed in [("/", True), ("/private", False), ("/private/public/info", True), ("/a?token=x", False),
                              ("/exact", True), ("/other", False), ("/PRIVATE", True)]:
            self.assertEqual(policy.allows(path), allowed, path)
        self.assertEqual(policy.interval, 5)

    def test_robots_unicode_and_reserved_percent_encodings(self):
        policy = parse_robots("User-agent: *\nDisallow: /%70rivate\nDisallow: /café\nDisallow: /a%2fb\n")
        self.assertFalse(policy.allows("/private"))
        self.assertFalse(policy.allows("/caf%C3%A9"))
        self.assertFalse(policy.allows("/a%2Fb"))
        self.assertTrue(policy.allows("/a/b"))
        for text in ["User-agent: *\nCrawl-delay: nan", "User-agent: *\nRequest-rate: 0/1", "x\n" * 2001]:
            with self.assertRaises(FetchError): parse_robots(text)

    def test_unknown_robots_directives_do_not_split_an_agent_group(self):
        policy = parse_robots("User-agent: TermsLens\nSitemap: https://policy.example.org/map.xml\nUser-agent: OtherBot\nDisallow: /private")
        self.assertFalse(policy.allows("/private"))

    def test_clean_text_retains_headings_exceptions_and_exact_unicode_offsets(self):
        raw = '<html lang="en-US"><head><title>Terms</title></head><nav>Menu</nav><h1>Policy 📜</h1><p>We retain data <strong>for 30 days</strong>.</p><h2>Exceptions</h2><p>Except when required by law.</p><script>private script</script><footer>Footer</footer></html>'.encode()
        document, _ = extract(raw, "text/html; charset=utf-8")
        self.assertEqual(document["text"], "Policy 📜\nWe retain data for 30 days.\nExceptions\nExcept when required by law.")
        for heading in document["headings"]:
            self.assertEqual(document["text"][heading["start"]:heading["end"]], heading["text"])
        self.assertEqual(document["title"], "Terms")

    def test_discovery_uses_navigation_links_without_fetching_or_combining_them(self):
        raw = b'<nav><a href="/privacy">Privacy</a><a href="/privacy#x">Duplicate</a><a href="https://external.example.org/terms">Terms</a><a href="http://127.0.0.1/terms">Terms</a><a href="/cookies">Cookies</a><a href="/refund">Refund</a><a href="/tos">Terms</a><a href="/returns">Returns</a><a href="javascript:alert(1)">Privacy</a></nav>'
        with self.assertRaisesRegex(FetchError, "empty_static_text"): extract(raw, "text/html")
        document, page = extract(raw, "text/html", discovery=True)
        links = candidates(page, validate_url("https://policy.example.org/"))
        self.assertEqual(document["text"], "")
        self.assertEqual([item["document_type"] for item in links], ["privacy", "cookies", "refund", "terms"])
        self.assertTrue(all(item["url"].startswith("https://policy.example.org/") for item in links))

    def test_unsupported_dynamic_and_oversized_sources_require_paste(self):
        cases = [(b"source", "application/pdf"), (b"source", None), (b"\xff", "text/plain; charset=utf-8"),
                 (b"<title>Just a moment...</title><p>Checking your browser</p>", "text/html"),
                 (b'<html lang="fr"><p>Politique</p></html>', "text/html"),
                 (b"<script>loadPolicy()</script>", "text/html"), (b"word " * 25001, "text/plain")]
        for body, media in cases:
            with self.subTest(media=media), self.assertRaises(FetchError): extract(body, media)


class FetchOperationTests(unittest.TestCase):
    def test_capture_returns_reproducible_hashes_and_two_budgeted_requests(self):
        fake = FakeFetch()
        operation = fake.operation()
        result = operation.run(URL)
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["raw_sha256"], sha256(HTML).hexdigest())
        self.assertEqual(result["text_sha256"], sha256(result["text"].encode()).hexdigest())
        self.assertEqual(result["raw_representation"], "decompressed_response_body")
        self.assertEqual([call[0].path for call in fake.calls], ["/robots.txt", "/terms"])
        with self.assertRaises(FetchError): operation.run(URL)

    def test_discovery_returns_candidates_without_fetching_selected_policies(self):
        fake = FakeFetch({URL: response(b'<nav><a href="/privacy">Privacy policy</a><a href="/cookies">Cookies</a></nav>')})
        result = fake.operation().run(URL, "discovery")
        self.assertEqual([item["document_type"] for item in result["candidates"]], ["privacy", "cookies"])
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("text", result)
        self.assertNotIn("raw_base64", result)

    def test_rebinding_after_robots_is_rejected_before_policy_request(self):
        fake = FakeFetch()
        operation = fake.operation()
        def resolve(target):
            return resolve_public(target, lambda *_args, **_kwargs: [answer(IP if not fake.calls else "127.0.0.1")])
        operation.resolve = resolve
        with self.assertRaisesRegex(FetchError, "nonpublic_address"): operation.run(URL)
        self.assertEqual(len(fake.calls), 1)

    def test_denied_or_unavailable_robots_never_fetch_the_policy(self):
        robots = "https://policy.example.org/robots.txt"
        for result in [response(b"User-agent: *\nDisallow: /", **{"Content-Type": "text/plain"}), response(b"", 403), response(b"", 503), response(b"<html>challenge</html>")]:
            fake = FakeFetch({robots: result})
            with self.assertRaises(FetchError): fake.operation().run(URL)
            self.assertEqual(len(fake.calls), 1)

    def test_redirects_recheck_robots_dns_and_consume_shared_eight_request_budget(self):
        pages = {}
        for old, new in [("policy", "second"), ("second", "third"), ("third", "fourth")]:
            pages[f"https://{old}.example.org/terms"] = response(b"", 302, Location=f"https://{new}.example.org/terms")
        fake = FakeFetch(pages)
        result = fake.operation().run(URL)
        self.assertEqual((result["requests"], result["redirects"]), (8, 3))
        self.assertEqual(len(fake.calls), 8)
        pages["https://fourth.example.org/terms"] = response(b"", 302, Location="https://fifth.example.org/terms")
        with self.assertRaisesRegex(FetchError, "redirect_limit"): FakeFetch(pages).operation().run(URL)

    def test_private_redirect_and_cross_origin_discovery_redirect_stop(self):
        for location, operation in [("http://169.254.169.254/", "capture"), ("https://other.example.org/", "discovery")]:
            fake = FakeFetch({URL: response(b"", 302, Location=location)})
            with self.assertRaises(FetchError): fake.operation().run(URL, operation)
            self.assertEqual(len(fake.calls), 2)

    def test_robots_redirect_hops_count_and_cannot_leave_origin(self):
        robots = "https://policy.example.org/robots.txt"
        fake = FakeFetch({robots: response(b"", 302, Location="/robots-new"),
            "https://policy.example.org/robots-new": response(b"User-agent: *\nCrawl-delay: 3", **{"Content-Type": "text/plain"})})
        result = fake.operation().run(URL)
        self.assertEqual((result["requests"], result["redirects"]), (3, 1))
        self.assertEqual(fake.slots[-1][1], 3)
        with self.assertRaisesRegex(FetchError, "cross_origin_redirect"):
            FakeFetch({robots: response(b"", 302, Location="https://other.example.org/robots.txt")}).operation().run(URL)

    def test_deadline_accounts_for_dns_waits_and_late_replies(self):
        for stage in ("dns", "wait", "http"):
            fake = FakeFetch()
            operation = fake.operation(timeout=2)
            if stage == "dns":
                def resolve(target): fake.now += 3; return fake.resolve(target)
                operation.resolve = resolve
            elif stage == "wait":
                @contextmanager
                def delayed(*_args): yield 3
                operation.host_slot = delayed
            else:
                def send(*args): fake.now += 3; return fake.send(*args)
                operation.send = send
            with self.subTest(stage=stage), self.assertRaises(FetchError): operation.run(URL)
            self.assertLessEqual(len(fake.calls), 1)

    def test_request_limit_is_enforced_before_any_network_call(self):
        fake = FakeFetch()
        operation = fake.operation()
        operation.requests = 8
        with self.assertRaisesRegex(FetchError, "request_limit"): operation.run(URL)
        self.assertEqual(fake.calls, [])


class HostThrottleTests(WorkerFixture):
    def test_host_lease_and_robots_interval_are_shared_across_instances(self):
        first, second = HostThrottle(self.factory), HostThrottle(self.factory)
        now = utcnow()
        with patch("app.url_throttle.utcnow", return_value=now):
            with first("policy.example.org", 1, 60) as delay:
                self.assertEqual(delay, 0)
                with self.assertRaisesRegex(FetchError, "host_busy"):
                    with second("policy.example.org", 1, 60): pass
            with second("policy.example.org", 5, 60) as delay: self.assertEqual(delay, 5)
            with self.assertRaisesRegex(FetchError, "host_throttled"):
                with first("policy.example.org", 1, 2): pass
        self.engine.dispose()
        with self.factory() as db:
            self.assertEqual(db.get(FetchHost, "policy.example.org").interval_seconds, 5)

    def test_failed_request_releases_lease_and_keeps_quiet_interval(self):
        throttle = HostThrottle(self.factory)
        now = utcnow()
        with patch("app.url_throttle.utcnow", return_value=now):
            with self.assertRaises(FetchError):
                with throttle("policy.example.org", 2, 60): raise FetchError("network_failed")
            with throttle("policy.example.org", 1, 60) as delay: self.assertEqual(delay, 2)
        with self.factory() as db: self.assertIsNone(db.get(FetchHost, "policy.example.org").lease_token)

    def test_expired_holder_cannot_release_new_holders_lease(self):
        throttle = HostThrottle(self.factory)
        old = throttle("policy.example.org", 1, 60)
        old.__enter__()
        with self.factory.begin() as db: db.get(FetchHost, "policy.example.org").lease_expires_at = utcnow() - timedelta(seconds=1)
        current = throttle("policy.example.org", 1, 60)
        current.__enter__()
        with self.factory() as db: token = db.get(FetchHost, "policy.example.org").lease_token
        old.__exit__(None, None, None)
        with self.factory() as db: self.assertEqual(db.get(FetchHost, "policy.example.org").lease_token, token)
        current.__exit__(None, None, None)


if __name__ == "__main__": unittest.main()
