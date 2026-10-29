"""One bounded capture/discovery operation over the pinned transport and shared throttle."""
from base64 import b64encode
from datetime import datetime, timezone
from hashlib import sha256
import math
from time import monotonic, sleep
from urllib.parse import urljoin

from .url_content import candidates, extract
from .url_guard import FetchError, MAX_BYTES, request_pinned, resolve_public, validate_url
from .url_robots import RobotsPolicy, parse_robots

MAX_REQUESTS = 8
MAX_REDIRECTS = 3
DEADLINE_SECONDS = 60
REDIRECTS = {301, 302, 303, 307, 308}


class FetchOperation:
    def __init__(self, host_slot, *, resolver=resolve_public, sender=request_pinned,
                 clock=monotonic, wait=sleep, timeout=DEADLINE_SECONDS):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= DEADLINE_SECONDS:
            raise FetchError("invalid_deadline")
        self.host_slot, self.resolve, self.send = host_slot, resolver, sender
        self.clock, self.wait, self.started = clock, wait, clock()
        self.deadline = self.started + timeout
        self.requests, self.redirects, self.policies = 0, 0, {}
        self.used = False

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0: raise FetchError("fetch_deadline")
        return remaining

    def request(self, target, interval):
        if self.requests >= MAX_REQUESTS: raise FetchError("request_limit")
        with self.host_slot(target.host, interval, self.remaining()) as delay:
            if not math.isfinite(delay) or not 0 <= delay < self.remaining(): raise FetchError("host_throttled")
            if delay: self.wait(delay)
            self.remaining()
            addresses = self.resolve(target)
            self.remaining()
            self.requests += 1
            response = self.send(target, addresses[0], self.remaining())
            self.remaining()
            if len(response.body) > MAX_BYTES: raise FetchError("response_too_large")
            return response

    def redirected(self, target, response, origin=None):
        if self.redirects >= MAX_REDIRECTS: raise FetchError("redirect_limit")
        location = response.headers.get("Location")
        if not location: raise FetchError("redirect_missing_location")
        destination = validate_url(urljoin(target.url, location))
        if origin is not None and destination.origin != origin: raise FetchError("cross_origin_redirect")
        self.redirects += 1
        return destination

    def robots(self, target):
        if target.origin in self.policies: return self.policies[target.origin]
        current = validate_url(f"{target.scheme}://{target.authority}/robots.txt")
        while True:
            response = self.request(current, 1)
            if response.status in REDIRECTS:
                current = self.redirected(current, response, target.origin)
                continue
            if response.status in {404, 410}:
                policy = RobotsPolicy()
            elif response.status in {401, 403}:
                raise FetchError("robots_denied")
            elif response.status == 200:
                if (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "text/plain":
                    raise FetchError("invalid_robots")
                try:
                    policy = parse_robots(response.body.decode("utf-8-sig"))
                except UnicodeError:
                    raise FetchError("invalid_robots") from None
            else:
                raise FetchError("robots_unavailable")
            self.policies[target.origin] = policy
            return policy

    def run(self, url, operation="capture"):
        if self.used or operation not in {"capture", "discovery"}: raise FetchError("invalid_operation")
        self.used = True
        original = target = validate_url(url)
        while True:
            policy = self.robots(target)
            if not policy.allows(target.path): raise FetchError("robots_denied")
            response = self.request(target, policy.interval)
            if response.status in REDIRECTS:
                target = self.redirected(target, response, original.origin if operation == "discovery" else None)
                continue
            if response.status != 200: raise FetchError("http_unavailable")
            document, page = extract(response.body, response.headers.get("Content-Type"), discovery=operation == "discovery")
            self.remaining()
            metadata = {"source_url": original.url, "final_url": target.url,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(), "requests": self.requests,
                        "redirects": self.redirects, "elapsed_seconds": round(self.clock() - self.started, 3)}
            if operation == "discovery":
                links = candidates(page, target)
                self.remaining()
                return {"operation": operation, **metadata, "candidates": links,
                        "limitations": ["static_same_origin_links_only"] + (["link_scan_limited"] if page.links_limited else [])
                                        + (["no_policy_links_found_use_paste"] if not links else [])}
            return {"operation": operation, **metadata, **document, "content_type": response.headers.get("Content-Type"),
                    "raw_representation": "decompressed_response_body", "raw_base64": b64encode(response.body).decode("ascii"),
                    "raw_sha256": sha256(response.body).hexdigest(), "text_sha256": sha256(document["text"].encode("utf-8")).hexdigest()}
