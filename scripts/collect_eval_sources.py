"""Collect curated public research documents; no app jobs, credentials or model calls."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "eval/source-candidates.json"
DESTINATION = ROOT / "var/eval/sources"
MAX_BYTES = 2 * 1024 * 1024
HOSTS = {"wordpress.com", "www.discourse.org", "www.mozilla.org", "creativecommons.org", "www.eff.org",
         "foundation.wikimedia.org", "osmfoundation.org", "www.blender.org", "console.groq.com", "groq.com"}
ID = re.compile(r"[a-z][a-z0-9-]{0,63}")


class CollectionError(Exception):
    """Fixed diagnostic code, never a raw response or exception."""


def documents(catalog):
    result = []
    for family in catalog["policy_families"]:
        if not ID.fullmatch(family["id"]) or family["split"] not in {"development", "heldout"}:
            raise ValueError("Invalid family")
        for item in family["documents"]:
            if item["document_type"] not in {"terms", "privacy", "cookies", "refund"}:
                raise ValueError("Invalid document type")
            result.append({**item, "role": "policy", "family": family["id"], "split": family["split"]})
    result.extend({**item, "role": "provider_reference"} for item in catalog["provider_documents"])
    ids = set()
    for item in result:
        url = urlsplit(item["url"])
        if (not ID.fullmatch(item["id"]) or item["id"] in ids or url.scheme != "https"
                or url.hostname not in HOSTS or url.port not in {None, 443}
                or url.username is not None or url.password is not None or url.query or url.fragment):
            raise ValueError("Invalid source candidate")
        ids.add(item["id"])
    return result


class PlainText(HTMLParser):
    """A research transcription, not a policy-aware application extractor."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}: self.hidden += 1
        if not self.hidden and tag in {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"}: self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

    def text(self):
        return "\n".join(line for raw in "".join(self.parts).splitlines() if (line := " ".join(raw.split())))


def fetch_one(item, destination, *, transport=None):
    """One GET, no redirects/retries/auth; the CLI parent enforces a total deadline."""
    try:
        with httpx.Client(transport=transport or httpx.HTTPTransport(retries=0), trust_env=False,
                          follow_redirects=False, timeout=5) as client:
            with client.stream("GET", item["url"], headers={"User-Agent": "TermsLens research/0.1",
                                                        "Accept-Encoding": "identity"}) as response:
                if 300 <= response.status_code < 400: raise CollectionError("redirect_not_followed")
                if response.status_code != 200: raise CollectionError("http_" + str(response.status_code))
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type not in {"text/html", "text/plain"}: raise CollectionError("unsupported_content_type")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise CollectionError("encoded_response")
                if "content-length" in response.headers and not 0 <= int(response.headers["content-length"]) <= MAX_BYTES:
                    raise CollectionError("source_too_large")
                raw = bytearray()
                for chunk in response.iter_raw():
                    if len(raw) + len(chunk) > MAX_BYTES: raise CollectionError("source_too_large")
                    raw.extend(chunk)
                decoded = raw.decode(response.encoding or "utf-8")
        if media_type == "text/html":
            parser = PlainText()
            parser.feed(decoded)
            parser.close()
            cleaned = parser.text()
        else:
            cleaned = decoded
        if not cleaned.strip(): raise CollectionError("empty_text")
    except CollectionError:
        raise
    except Exception:
        raise CollectionError("fetch_failed") from None
    metadata = {**item, "retrieved_at": datetime.now(timezone.utc).isoformat(), "content_type": media_type,
                "raw_sha256": sha256(raw).hexdigest(), "text_sha256": sha256(cleaned.encode("utf-8")).hexdigest(),
                "extractor": "research-html-text-v1", "rights_review": "pending", "content_review": "pending"}
    # Each acquisition is immutable. A capture.json written last marks a complete capture.
    capture = Path(destination) / item["id"] / uuid4().hex
    try:
        capture.mkdir(parents=True, exist_ok=False)
        (capture / "raw.bin").write_bytes(raw)
        (capture / "text.txt").write_text(cleaned, encoding="utf-8", newline="")
        (capture / "capture.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        raise CollectionError("capture_write_failed") from None
    return capture


def collect_in_child(source_id):
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", source_id],
            cwd=ROOT, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        status = json.loads(result.stdout) if len(result.stdout) <= 2048 else {}
        if status.get("id") != source_id or status.get("status") not in {"captured", "failed"}:
            raise ValueError("Invalid child status")
        if result.returncode != (0 if status["status"] == "captured" else 1):
            raise ValueError("Child status disagrees with exit")
        return status
    except subprocess.TimeoutExpired:
        return {"id": source_id, "status": "failed", "reason": "total_timeout"}
    except Exception:
        return {"id": source_id, "status": "failed", "reason": "child_failed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--fetch", nargs="+", metavar="SOURCE_ID", help="Fetch named catalog entries (or all); opt-in network access")
    choice.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    entries = documents(json.loads(CATALOG.read_text(encoding="utf-8")))
    by_id = {item["id"]: item for item in entries}
    selected = [args.child] if args.child else list(by_id) if args.fetch == ["all"] else args.fetch
    if selected is None:
        for item in entries: print(json.dumps(item))
        print("UNVERIFIED candidates only; no network requests or source captures.")
        return
    if any(source_id not in by_id for source_id in selected): parser.error("Choose source IDs from the catalog")
    failed = False
    for source_id in dict.fromkeys(selected):
        if args.child:
            try:
                capture = fetch_one(by_id[source_id], DESTINATION)
                status = {"id": source_id, "status": "captured", "capture": capture.relative_to(ROOT).as_posix()}
            except CollectionError as error:
                status = {"id": source_id, "status": "failed", "reason": str(error)}
        else:
            status = collect_in_child(source_id)
        print(json.dumps(status), flush=True)
        failed |= status["status"] == "failed"
        if failed: break  # Fix the observed blocker before spending more requests.
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__": main()
